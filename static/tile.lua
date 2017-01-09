local api, CHILDS, CONTENTS = ...

local M = {}

local files = {}

local min = math.min

local function ramp(t_s, t_e, t_c, ramp_time)
    if ramp_time == 0 then return 1 end
    local delta_s = t_c - t_s
    local delta_e = t_e - t_c
    return min(1, delta_s * 1/ramp_time, delta_e * 1/ramp_time)
end

local black = resource.create_colored_texture(0,0,0,1)
local github = resource.load_image(api.localized "github.png")
local font
local size = 40
local fade_time = 0.4
local call = ""

local function mk_overlay(file)
    -- local text = "/" .. file.username
    -- local h = size
    -- local w = h + 20 + font:width(text, h-10)
    -- return function(x1, y1, x2, y2, alpha)
    --     black:draw(x2-w, y2-h, x2, y2, 0.5*alpha)
    --     github:draw(x2-w, y2-h, x2-w+h, y2, alpha)
    --     font:write(x2-w+h+10, y2-h+5, text, h-10, 1,1,1,alpha)
    -- end
    
    local text = file.asset_type .. "/" .. file.asset_id .." by github.com/" .. file.username .. " - " .. call
    local w = font:width(text, size-10)
    return function(x1, y1, x2, y2, alpha)
        black:draw(x1, y2-size, x1+w+20, y2, alpha/1.4)
        font:write(x1+10, y2-size+5, text, size-10, 1,1,1,alpha)
    end
end

function M.updated_config_json(config)
    font = resource.load_font(api.localized(config.font.asset_name))
    size = config.size
    call = config.call
end

function M.updated_files_json(new_files)
    files = new_files
    for idx = 1, #files do
        local file = files[idx]
        file.asset = resource.open_file(api.localized(file.asset_name))
    end
end

local function show_your_content(starts, ends)
    print("SHOWING YOUR CONTENT")
end
                                                                                                                                                                                                                    
local function show_user_video(starts, ends, file)
    api.wait_t(starts - 2)

    local raw = sys.get_ext "raw_video"
    local vid = raw.load_video{
        file = file.asset:copy(),
        paused = true,
    }

    local overlay = mk_overlay(file)

    for now, x1, y1, x2, y2 in api.from_to(starts, ends) do
        local alpha = ramp(starts, ends, now, fade_time)
        vid:target(x1, y1, x2, y2):alpha(alpha):layer(-1):start()
        overlay(x1, y1, x2, y2, alpha)
    end

    vid:dispose()
end

local function show_user_image(starts, ends, file)
    api.wait_t(starts - 2)

    local img = resource.load_image(file.asset:copy())
    local overlay = mk_overlay(file)

    for now, x1, y1, x2, y2 in api.from_to(starts, ends) do
        local alpha = ramp(starts, ends, now, fade_time)
        util.draw_correct(img, x1, y1, x2, y2, alpha)
        overlay(x1, y1, x2, y2, alpha)
    end

    img:dispose()
end

local last_asset_id = 0

function M.task(starts, ends, custom)                                                                                                                                                                               
    local candidates = {}

    local now = api.clock.get_unix()
    for idx = 1, #files do
        local file = files[idx]
        if now >= file.starts and now <= file.ends then
            candidates[#candidates+1] = file
        end
    end

    print("found " .. #candidates .. " candidates to show")

    if #candidates == 0 then
        return show_your_content(starts, ends)
    end

    local span = {}
    local total = 1
    for idx = 1, #candidates do
        local candidate = candidates[idx]
        local prio = math.ceil(candidate.prio * 100)
        if candidate.asset_id == last_asset_id then
            prio = math.ceil(prio / 10)
        end
        total = total + prio
        span[idx] = total
    end

    local file
    local selected = math.random(total)
    for idx = 1, #candidates do
        if selected <= span[idx] then
            file = candidates[idx]
            break
        end
    end

    pp(span)
    print("selected " .. selected)

    if not file then
        error("hu?")
    end

    last_asset_id = file.asset_id

    print("showing " .. file.asset_id .. ", made by " .. file.username)

    if file.asset_type == "video" then
        return show_user_video(starts, ends, file)
    else
        return show_user_image(starts, ends, file)
    end
end                                                                                                                                                                                                                 
                                                                                                                                                                                                                    
return M  

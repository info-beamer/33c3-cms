import os
import uuid
import math
import logging
import tempfile
import random
import traceback
import time, datetime, calendar
import smtplib, textwrap, base64
from itertools import izip
from collections import defaultdict
from email.mime.text import MIMEText
from hmac import HMAC

import Image
import iso8601
from ffvideo import VideoStream

from flask import Flask, request, g, session, redirect, url_for, abort, jsonify
from flask import render_template
from flask_github import GitHub
from flask_restful import Resource, Api, fields, marshal_with
from werkzeug.exceptions import HTTPException
from werkzeug.contrib.fixers import ProxyFix
from werkzeug.routing import BaseConverter, ValidationError

from sqlalchemy import create_engine, Column, Integer, String, ForeignKey
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.ext.declarative import declarative_base

class AssetError(HTTPException):
    code = 415

Image.init()
Image.ID = ("JPEG",) # only support jpeg

log = logging.getLogger('main')

logging.basicConfig()
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)
logging.getLogger('main').setLevel(logging.INFO)

THUMB_SIZE = 320, 180

def utc_to_unix(*args):
    d = datetime.datetime(*args)
    return int(calendar.timegm(d.timetuple()))

START_TS = utc_to_unix(2016, 12, 27, 0)
END_TS   = utc_to_unix(2016, 12, 30,18)

VERSION = os.getenv("VERSION", str(int(time.time()*1000)))

# START_TS = utc_to_unix(1970, 1, 1, 0)
# END_TS   = utc_to_unix(1970, 1, 1, 23)

INTERVAL = 60*30

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app)
app.jinja_options = app.jinja_options.copy()
app.jinja_options.update(dict(
    variable_start_string = '<%',
    variable_end_string = '%>',
))

app.config.update(dict(
    DEBUG = os.getenv('DEBUG') == 'yes',

    DATABASE_URI = 'sqlite:///db/db',

    SESSION_COOKIE_SECURE = True,

    PREFERRED_URL_SCHEME = 'https',
    SERVER_NAME = os.getenv('SERVER_NAME', '33c3.infobeamer.com'),
    SEND_FILE_MAX_AGE_DEFAULT = 60000,

    MAX_CONTENT_LENGTH = 32*1024*1024,
))
app.config.from_envvar("SETTINGS")

api = Api(app)
github = GitHub(app)

engine = create_engine(app.config['DATABASE_URI'])
db_session = scoped_session(sessionmaker(autocommit=False,
                                         autoflush=False,
                                         bind=engine))
Base = declarative_base()
Base.query = db_session.query_property()

class SignedConverter(BaseConverter):
    def __init__(self, map, scope='', size=8):
        BaseConverter.__init__(self, map)
        self.key = str("%s|%s" % (app.config['SECRET_KEY'], scope))
        self.size = size

    def to_python(self, token):
        if not '~' in token:
            raise ValidationError()
        value, signature = token.rsplit('~', 1)
        try:
            valid_sig = base64.urlsafe_b64encode(HMAC(self.key, str(value)).digest())[:self.size]
        except Exception:
            raise ValidationError()
        if valid_sig != signature:
            raise ValidationError()
        return value

    def to_url(self, value):
        value = str(value)
        signature = base64.urlsafe_b64encode(HMAC(self.key, value).digest())[:self.size]
        return "%s~%s" % (value, signature)

app.url_map.converters['signed'] = SignedConverter

def init_db():
    Base.metadata.create_all(bind=engine)

def random32():
    return uuid.uuid4().hex

def generate_csrf_token():
    if not 'csrf' in session:
        session['csrf'] = random32()
    return session['csrf']

app.jinja_env.globals['csrf'] = generate_csrf_token 

class User(Base):
    __tablename__ = 'user'
    __table_args__ = {'sqlite_autoincrement': True}

    id = Column(Integer, primary_key=True)
    followers = Column(Integer)
    username = Column(String(200))
    github_access_token = Column(String(200))

class Asset(Base):
    __tablename__ = 'asset'
    __table_args__ = {'sqlite_autoincrement': True}

    TYPE_IMAGE = 0
    TYPE_VIDEO = 1

    MODERATE_UNKNOWN = 0
    MODERATE_OK = 1
    MODERATE_NOPE = 2

    @classmethod
    def type_string(cls, asset):
        return {
            cls.TYPE_IMAGE: 'image',
            cls.TYPE_VIDEO: 'video',
        }[asset.asset_type]

    @classmethod
    def type_extension(cls, asset):
        return {
            cls.TYPE_IMAGE: 'jpg',
            cls.TYPE_VIDEO: 'mp4',
        }[asset.asset_type]

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id'))
    secret = Column(String(32))
    asset_type = Column(Integer)
    status = Column(Integer)
    starts = Column(Integer)
    ends = Column(Integer)

def make_thumb(im):
    im.thumbnail(THUMB_SIZE, resample=Image.ANTIALIAS)
    size = im.size
    thumb = Image.new('RGB', THUMB_SIZE, (255, 0, 0, 255))
    thumb.paste(im, (
        (THUMB_SIZE[0] - size[0]) /2,
        (THUMB_SIZE[1] - size[1]) /2,
    ))
    return thumb

def send_mail(asset_id, username):
    msg = MIMEText(textwrap.dedent("""
        New content uploaded by user github.com/%(username)s

        Moderate here: %(url)s
    """).strip() % dict(
        url = url_for("moderate", asset_id=asset_id, _external=True),
        username = username,
    ), "plain", "utf8")
    msg['Subject'] = "Content by user github.com/" + username
    msg['From'] = app.config['SMTP_USER']
    msg['To'] = app.config['SMTP_USER']
    msg['Bcc'] = ",".join(app.config['SMTP_TO'])

    server = smtplib.SMTP(app.config['SMTP_HOST'], 2525)
    server.set_debuglevel(1)
    server.ehlo()
    server.starttls()
    server.ehlo()
    server.login(app.config['SMTP_USER'], app.config['SMTP_PASS'])
    server.sendmail(app.config['SMTP_USER'], app.config['SMTP_TO'], msg.as_string())
    server.quit()
    server.close()

def verify_file(orig_filename, filename):
    name, ext = os.path.splitext(orig_filename)
    ext = ext.lower()
    if ext in ('.jpg', '.jpeg'):
        im = Image.open(filename)
        w, h = im.size
        if w > 1920 or h > 1080:
            raise AssetError("image too big: %dx%d" % (w, h))
        if w < 128 or h < 128:
            raise AssetError("image too small: %dx%d" % (w, h))
        if im.mode != 'RGB':
            raise AssetError("invalid image mode: %s" % im.mode)
        return Asset.TYPE_IMAGE, make_thumb(im)
    elif ext in ('.mp4',):
        vs = VideoStream(filename)
        if vs.codec_name != 'h264':
            raise AssetError("invalid codec %s" % (vs.codec_name,))
        w, h = vs.frame_width, vs.frame_height
        duration = vs.duration
        log.info("video info: %dx%d (%fs)" % (w, h, duration))
        if w > 1920 or h > 1080:
            raise AssetError("video too big: %dx%d" % (w, h))
        if w < 128 or h < 128:
            raise AssetError("video too small: %dx%d" % (w, h))
        if duration < 1:
            raise AssetError("video too short: %fs" % (duration,))
        if duration > 10.5:
            raise AssetError("video too long: %fs" % (duration,))
        frame = vs.get_frame_no(3)
        im = frame.image()
        return Asset.TYPE_VIDEO, make_thumb(im)
    else:
        raise AssetError("invalid asset file extension")

@app.before_request
def before_request():
    g.user = None
    # if app.config['DEBUG']:
    #     session['user_id'] = 1

    # XXX: comment out to force logout
    if 'user_id' in session:
        g.user = User.query.get(session['user_id'])

    if request.method == "POST":
        token = session.get('csrf')
        if not token or token != request.form.get('csrf'):
            abort(400)
        log.info("CSRF looks good")

@app.after_request
def after_request(response):
    db_session.remove()
    return response

@github.access_token_getter
def token_getter():
    user = g.user
    if user is not None:
        return user.github_access_token


@app.route('/github-callback')
@github.authorized_handler
def authorized(access_token):
    next_url = request.args.get('next') or url_for('index')
    if access_token is None:
        return redirect(next_url)

    state = request.args.get('state')
    if state != session.get('state'):
        return redirect(url_for('index'))
    session.pop('state')

    github_user = github.get('user', access_token=access_token)
    # import pprint; pprint.pprint(github_user)
    username = github_user['login']

    age = datetime.datetime.utcnow() - iso8601.parse_date(
        github_user['created_at']
    ).replace(tzinfo=None)

    log.info("user is %d days old" % age.days)
    if age.days < 31:
        return redirect(url_for('sorry', reason='new'))

    log.info("user has %d followers" % github_user['followers'])
    if github_user['followers'] < 5:
        return redirect(url_for('sorry', reason='followers'))

    user = User.query.filter_by(username=username).first()
    if user is None:
        user = User(username=username)
        db_session.add(user)

    user.github_access_token = access_token
    user.followers = github_user['followers']
    db_session.commit()

    session['user_id'] = user.id
    return redirect(next_url)

@app.route('/login')
def login():
    if session.get('user_id', None) is None:
        session['state'] = state = random32()
        return github.authorize(state=state)
    else:
        return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/asset/%s/thumb/<int:id>.jpg' % VERSION)
def asset_thumb(id):
    asset = checked_asset(id)
    return redirect(url_for('static', filename='asset-%s-%s.thumb.jpg' % (
        asset.id, asset.secret
    )))

@app.route('/asset/%s/full/<int:id>' % VERSION)
def asset_full(id):
    asset = checked_asset(id)
    return redirect(url_for('static', filename='asset-%s-%s.%s' % (
        asset.id, asset.secret, Asset.type_extension(asset),
    )))

def checked_user():
    user = g.user
    if not user:
        raise(401)
    return user

def checked_asset(asset_id):
    user = checked_user()
    asset = Asset.query.filter_by(id=asset_id, user_id=user.id).first()
    if not asset:
        abort(401)
    return asset

asset_fields = {
    'id': fields.Integer,
    'url': fields.Url('asset'),
    'thumb': fields.Url('asset_thumb'),
    'full': fields.Url('asset_full'),
    'status': fields.Integer,
    'type': fields.String(attribute=Asset.type_string),
    'starts': fields.Integer,
    'ends': fields.Integer,
}

asset_list_fields = {
    'assets': fields.List(fields.Nested(asset_fields))
}

class AssetResource(Resource):
    @marshal_with(asset_fields)
    def get(self, id):
        asset = checked_asset(id)
        return asset

    @marshal_with(asset_fields)
    def patch(self, id):
        asset = checked_asset(id)
        json = request.get_json()
        starts = json['starts']
        ends = json['ends']
        starts = max(START_TS, min(starts, ends))
        ends = min(END_TS, max(starts + INTERVAL, ends))
        asset.starts = starts
        asset.ends = ends
        db_session.commit()
        return asset

    def delete(self, id):
        asset = checked_asset(id)
        db_session.delete(asset)
        db_session.commit()
        return {'deleted': asset.id}

class AssetListResource(Resource):
    @marshal_with(asset_list_fields)
    def get(self):
        user = checked_user()
        assets = Asset.query.filter_by(user_id=user.id).order_by('id').all()
        return {'assets': assets}

    @marshal_with(asset_list_fields)
    def post(self):
        user = checked_user()
        if not 'file' in request.files:
            raise AssetError("no file provided")
        upload = request.files['file']
        if upload.filename == '':
            raise AssetError("empty asset filename")
        secret = random32()

        tmpfile = tempfile.NamedTemporaryFile()
        upload.save(tmpfile)
        tmpfile.flush()
        asset_type, thumb = verify_file(upload.filename, tmpfile.name)

        asset = Asset(
            user_id = user.id,
            secret = secret,
            asset_type = asset_type,
            status = Asset.MODERATE_UNKNOWN,
            starts = 0,
            ends = 0,
        )
        db_session.add(asset)
        db_session.flush()

        send_mail(asset.id, user.username)
        asset_name = "%d-%s" % (asset.id, asset.secret)
        try:
            os.rename(tmpfile.name, "static/asset-%s.%s" % (asset_name, Asset.type_extension(asset)))
            tmpfile.delete = False

            with file("static/asset-%s.thumb.jpg" % asset_name, "wb") as f:
                thumb.save(f, format='jpeg')
        except:
            traceback.print_exc()
            raise AssetError("saving asset failed")

        db_session.commit()
        assets = Asset.query.filter_by(user_id=user.id).order_by('id').all()
        return {'assets': assets}

api.add_resource(AssetListResource, '/api/asset')
api.add_resource(AssetResource,     '/api/asset/<int:id>', endpoint='asset')

@app.route('/')
def index():
    if g.user:
        return redirect(url_for('dashboard'))
    now = time.time()
    live = db_session.query(Asset, User).filter(
        Asset.user_id == User.id,
        Asset.status == Asset.MODERATE_OK,
        Asset.starts < now,
        Asset.ends > now,
    ).all()
    if live:
        random.shuffle(live)
    return render_template('index.jinja',
        live = live,
    )

@app.route('/nope')
def sorry():
    return render_template('sorry.jinja',
        reason = request.values.get('reason')
    )

@app.route('/dashboard')
def dashboard():
    if not g.user:
        return redirect(url_for('login'))

    return render_template('dashboard.jinja',
        username = g.user.username,
        earliest = START_TS,
        latest = END_TS,
        interval = INTERVAL,
        version = VERSION,
    )

@app.route('/export/infobeamer/package.links')
def export():
    def f(filename, static):
        return "%s %s" % (filename, url_for(
            'static', filename=static, _external=True,
        ))
    lines = [
        "[info-beamer-links]",
        f("package.png", "package.png"),
        f("package.json", "package.json"),
        f("node.json", "node.json"),
        f("node.lua", "node.lua"),
        f("tile.lua", "tile.lua"),
        f("default-font.ttf", "default-font.ttf"),
        f("github.png", "github.png"),
        "files.json %s" % url_for("export_files_json", _external=True, _scheme='https'),
    ]
    assets = Asset.query.filter(
        Asset.status == Asset.MODERATE_OK,
    ).all()
    for asset in assets:
        lines.append(f(asset.secret, "asset-%d-%s.%s" % (
            asset.id, asset.secret, Asset.type_extension(asset),
        )))
    return ''.join(line + '\n' for line in lines)

@app.route('/export/infobeamer/files.json')
def export_files_json():
    files = db_session.query(Asset, User).filter(
        Asset.user_id == User.id,
        Asset.status == Asset.MODERATE_OK,
        Asset.starts != 0,
        Asset.ends != 0,
    ).all()

    if not files:
        return jsonify([])

    # I have no idea what I'm doing %)
    # Goals:
    #   User with more screen time total => less weight
    #      compared to a user with a single file
    #   Narrower targeted file => higher priority compared
    #      to content that runs all the time
    user_duration = defaultdict(int)
    for asset, user, in files:
        user_duration[user.id] += asset.ends - asset.starts

    total_duration = sum(user_duration.values())

    user_weight = defaultdict(float)
    for user_id, duration in user_duration.iteritems():
        user_weight[user_id] = math.log(3.2 - 1. / total_duration * duration)
    print user_weight

    def calc_weight(asset):
        duration = asset.ends - asset.starts
        asset_weight = 8. / math.log(10 + duration)
        weight = asset_weight * user_weight[asset.user_id]
        print asset.id, duration, "asset:", asset_weight, "user:", user_weight[asset.user_id], "->", weight
        return weight

    asset_weights = [
        calc_weight(asset) for asset, user in files
    ]

    max_weight = max(asset_weights)
    asset_weights = [
        max(0.2, 1 / max_weight * weight)
        for weight in asset_weights
    ]

    return jsonify([
        dict(
            asset_id = asset.id,
            username = user.username,
            starts = asset.starts,
            ends = asset.ends,
            asset_name = asset.secret,
            asset_type = Asset.type_string(asset),
            prio = prio,
        ) for (asset, user), prio in izip(files, asset_weights)
    ])

@app.route('/moderate/<signed(scope="moderate"):asset_id>')
def moderate(asset_id):
    asset_id = int(asset_id)
    asset = Asset.query.filter_by(id=asset_id).first()
    if not asset:
        abort(404)
    return render_template('moderate.jinja',
        asset = asset,
    )

@app.route('/moderate/<signed(scope="moderate"):asset_id>/save')
def moderate_set(asset_id):
    asset_id = int(asset_id)
    asset = Asset.query.filter_by(id=asset_id).first()
    if not asset:
        abort(404)
    if asset.status == Asset.MODERATE_NOPE:
        abort(401) # can't revive
    status = request.values.get('status', type=int)
    if status not in (Asset.MODERATE_OK, Asset.MODERATE_NOPE):
        abort(401)
    asset.status = status
    db_session.add(asset)
    db_session.commit()
    return redirect(url_for('index'))

@app.route('/robots.txt')
def robots_txt():
    return "User-Agent: *\nDisallow: /\n"

if __name__ == '__main__':
    init_db()
    app.run(port=10000)

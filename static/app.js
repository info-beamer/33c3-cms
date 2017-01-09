'use strict';

const store = new Vuex.Store({
  strict: true,

  state: {
    assets: [],
    username: username,
    earliest: earliest,
    latest: latest,
    interval: interval,
    now: 0,
  },

  mutations: {
    updateAssets (state, assets) {
      state.assets = assets;
    },

    removeAsset (state, asset_id) {
      for (var idx = 0; idx < state.assets.length; idx++) {
        const asset = state.assets[idx]
        if (asset.id == asset_id) {
          state.assets.splice(idx, 1);
        }
      }
      return state;
    },

    updateAsset (state, {asset_id, asset}) {
      console.log("update asset", asset_id, asset);
      for (var idx = 0; idx < state.assets.length; idx++) {
        const old_asset = state.assets[idx]
        if (old_asset.id == asset_id) {
          console.log("updated asset!");
          Vue.set(state.assets, idx, asset);
          // state.assets.splice(idx, 1, asset);
        }
      }
      return state;
    },

    updateNow (state, timestamp) {
      console.log("tick!", timestamp);
      state.now = timestamp;
      return state
    },
  },

  actions: {
    refresh (context) {
      axios.get('/api/asset').then(function (response) {
        context.commit('updateAssets', response.data.assets);
      });
    },

    upload (context, file) {
      var upload = new FormData();
      upload.append('file', file);
      upload.append('csrf', csrf);

      axios.post('/api/asset', upload).then(function (response) {
        context.commit('updateAssets', response.data.assets);
        alert("Success. Don't forget to set start/end time for your upload.");
      }).catch(function (error) {
        if (error.response.status == 415) {
            alert("Cannot use this file: " + error.response.data.message);
        } else {
            alert(error);
        }
      });
    },

    delete (context, asset_id) {
      axios.delete('/api/asset/' + asset_id).then(function (response) {
        context.commit('removeAsset', asset_id);
      });
    },

    setTime(context, {asset_id, starts, ends}) {
      axios.patch('/api/asset/' + asset_id, {
        starts: starts,
        ends: ends,
      }).then(function (response) {
        context.commit('updateAsset', {
          asset_id: asset_id, 
          asset: response.data
        });
      });
    },

    updateNow (context) {
      context.commit('updateNow', Math.floor(Date.now() / 1000));
    },
  },
})

function ts_to_text(ts) {
  const date = new Date(ts*1000);
  const day = date.getDate();
  const month = date.getMonth() + 1;
  const hours = date.getHours();
  const minutes = "0" + date.getMinutes();
  return day + "." + month + ". " + hours + ':' + minutes.substr(-2);
}

Vue.component('time-select', {
  template: '#time-select',
  props: ['timestamp', 'from', 'to'],
  computed: {
    options() {
      var options = [{
        value: 0,
        text: '-',
      }]
      console.log(this.from, this.to);
      for (var timestamp = this.from;
           timestamp <= this.to;
           timestamp += this.$store.state.interval
      ) {
        options.push({
          value: timestamp,
          text: ts_to_text(timestamp),
          selected: this.timestamp == timestamp,
        })
      }
      return options;
    }
  },
  methods: {
    onUpdate: function(evt) {
      this.$emit('timeSelected', parseInt(evt.target.value));
    }
  }
})

Vue.component('asset-item', {
  template: '#asset-item',
  props: ['asset'],
  computed: {
    type() {
      return this.asset.type == "video" ? "Video" : "Image";
    },
    start_from() {
      return this.$store.state.earliest;
    },
    start_to() {
      return this.$store.state.latest;
    },
    end_from() {
      return Math.max(this.asset.starts + this.$store.state.interval,
                      this.$store.state.earliest);
    },
    end_to() {
      return this.$store.state.latest;
    },
    play_info() {
      const now = this.$store.state.now;
      const starts_in = this.asset.starts - now;
      const obsolete = this.asset.ends < now; 
      const unset = this.asset.starts == 0;
      if (unset) {
        return 'Unconfigured';
      } else if (obsolete) {
        return 'Completed';
      } else if (starts_in > 0) {
        return 'Starting in ' + Math.floor(starts_in / 60) + " min";
      } else {
        return 'On the screens';
      }
    },
    moderation() {
      if (this.asset.status == 0) {
        return "Pending";
      } else if (this.asset.status == 1) {
        return "Approved";
      } else if (this.asset.status == 2) {
        return "Denied";
      }
    },
  },
  methods: {
    onUpdateStart: function(timestamp) {
      this.$store.dispatch('setTime', {
        asset_id: this.asset.id, 
        starts: timestamp,
        ends: Math.max(timestamp, this.asset.ends),
      });
    },
    onUpdateEnd: function(timestamp) {
      this.$store.dispatch('setTime', {
        asset_id: this.asset.id, 
        starts: Math.min(timestamp, this.asset.starts),
        ends: timestamp,
      });
    },
    onDelete: function(evt) {
      this.$store.dispatch('delete', this.asset.id);
    }
  }
})

Vue.component('asset-list', {
  template: '#asset-list',
  computed: {
    assets() {
      return this.$store.state.assets;
    }
  },
  methods: {
    onRefresh () {
      this.$store.dispatch('refresh');
    }
  }
})

Vue.component('asset-uploader', {
  template: '#asset-uploader',
  data: () => ({
    file: null,
  }),
  computed: {
    has_file() {
      return this.file != null;
    },
    filename () {
      return this.file ? this.file.name : '';
    }
  },
  methods: {
    onFileSelected: function(evt) {
      var files = evt.target.files || evt.dataTransfer.files;
      if (!files.length)
        return;
      var file = files[0];
      this.file = file;
    },
    onUpload: function(evt) {
      this.$store.dispatch('upload', this.file);
      this.file = null;
    },
    onAbort: function(evt) {
      this.file = null;
    }
  }
})

const app = new Vue({
  el: "#app",
  store,
  computed: {
    username() {
      return this.$store.state.username;
    }
  }
})

setInterval(function() {
  store.dispatch('updateNow');
}, 10*1000);

store.dispatch('updateNow');
store.dispatch('refresh');

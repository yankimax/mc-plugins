# External Optional Plugins

This directory is the repo-level source root for optional/external MinaChan plugins.

Layout contract:

- `minachan_app/plugins/`
  - bundled/internal baseline plugins and SDK files only
- `plugins/`
  - optional/external plugin source repository
  - each plugin uses Cinnamon-style layout:
    - `<pluginId>/info.json`
    - `<pluginId>/files/<pluginId>/manifest.json`
    - `<pluginId>/files/<pluginId>/plugin.py3`
    - `<pluginId>/test_*.py`

Runtime contract:

- the app loads bundled/internal plugins from `minachan_app/plugins`
- the app loads user-installed plugins from `minachan_app/data/plugins/installed`
- the app does not auto-discover sibling optional plugins from this directory during `flutter run`
- SDK files stay in `minachan_app/plugins/sdk_*`
- installed/external Python plugins must import SDK through `MINACHAN_SDK_PYTHON_DIR`
- runtime provides `MINACHAN_SDK_PYTHON_DIR` for external plugins automatically

Repository contract:

- this repo stores plugin source trees, not committed install archives
- do not commit `packages/*.zip`
- do not treat `catalog.json` in git as the source of truth
- do not copy SDK files into this repo

Required manifest contract:

- `manifestVersion: 2`
- semantic `version`
- `runtimeApi: "3"`
- `platforms`
- `bootRole`
- `deps`

Temporary builder path:

```bash
cd ../minachan_runtime
dart run tool/build_plugin_catalog.dart \
  --plugins-dir ../plugins \
  --output-dir /tmp/minachan_plugin_catalog_build \
  --source-id community.example
```

Plugin repo test path:

```bash
export MINACHAN_SDK_PYTHON_DIR=../minachan_app/plugins/sdk_python
for d in ./*; do
  if [ -d "$d" ] && find "$d" -maxdepth 1 -name 'test_*.py' | grep -q .; then
    python3 -m unittest discover -s "$d" -p 'test_*.py'
  fi
done
```

Repository-level verification routine:

```bash
tools/ci/run_plugin_system_verify.sh
```

This directory must not contain bundled baseline plugins or SDK copies.

# External Optional Plugins

This directory is the repo-level source root for optional/external MinaChan plugins.

Layout contract:

- `minachan_app/plugins/`
  - bundled/internal baseline plugins and SDK files only
- `plugins/`
  - optional/external plugins used for catalog build and install/update testing

Runtime contract:

- the app loads bundled/internal plugins from `minachan_app/plugins`
- the app loads user-installed plugins from `minachan_app/data/plugins/installed`
- the app does not auto-discover sibling optional plugins from this directory during `flutter run`
- SDK files stay in `minachan_app/plugins/sdk_*`; installed/external plugins get access to them through the runtime launch environment

Required manifest contract:

- `manifestVersion: 2`
- semantic `version`
- `runtimeApi: "3"`
- `platforms`
- `bootRole`
- `deps`

Official catalog build path:

```bash
cd ../minachan_runtime
dart run tool/build_plugin_catalog.dart \
  --plugins-dir ../plugins \
  --output-dir /tmp/minachan_plugin_catalog_build \
  --package-base-url https://raw.githubusercontent.com/yankimax/mc-plugins/master/packages/
```

Repository-level verification routine:

```bash
tools/ci/run_plugin_system_verify.sh
```

This directory must not contain bundled baseline plugins or SDK copies.

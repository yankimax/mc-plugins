# External Optional Plugins

This directory is the repo-level development root for optional/external MinaChan plugins.

Layout contract:

- `minachan_app/plugins/`
  - bundled/internal baseline plugins and SDK files only
- `plugins/`
  - optional/external plugins used in local development

Runtime contract in dev mode:

- the app loads bundled/internal plugins from `minachan_app/plugins`
- the app also discovers optional plugins from this directory
- SDK files stay in `minachan_app/plugins/sdk_*`; external plugins get access to them through the runtime launch environment

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

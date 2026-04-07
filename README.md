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

This directory must not contain bundled baseline plugins or SDK copies.

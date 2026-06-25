# Weather Forecast Server

This repository is the public AGPL weather forecast server for the Singapore
weather-model node.

The migration target is to use the Open-Meteo engine directly instead of
maintaining a Python reimplementation of Open-Meteo weather logic. Our local
code is limited to:

- China and surrounding-region domain selection.
- GFS download source configuration and regional slicing for a lightweight
  server.
- Point-package and layer-product export formats used by our clients.
- Deployment, scheduling, and validation tooling.

The previous private repository `89199156-design/weather_server_gfs` is
read-only migration input. It must not be modified or pushed during this
migration.

## License

This project is licensed under GNU Affero General Public License v3.0 or later.
The Open-Meteo upstream source is AGPL. Commercial use is allowed under the
license, but network use of modified server software requires source
availability for the corresponding modified version.

See [LICENSE](LICENSE) and [UPSTREAM.md](UPSTREAM.md).

## Current Scope

Singapore keeps weather-model processing only. Satellite code is intentionally
excluded because it has been split to another server.

The implementation order is:

1. Vendor and document the Open-Meteo engine baseline.
2. Add our regional GFS download/source configuration.
3. Export point API packages from Open-Meteo-derived data.
4. Export layers from the same Open-Meteo-derived data.
5. Deploy to Singapore and remove old satellite code/tasks there.
6. Validate 50, then 100, then 500 points x 50 forecast frames for point API
   and layer consistency.

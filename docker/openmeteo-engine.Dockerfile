FROM ghcr.io/open-meteo/docker-container-build:latest AS build

WORKDIR /build
COPY vendor/openmeteo-sdk /build/openmeteo-sdk
COPY vendor/open-meteo/Package.* /build/open-meteo/

WORKDIR /build/open-meteo
RUN ENABLE_PARQUET=TRUE swift package resolve

COPY vendor/open-meteo /build/open-meteo
RUN ENABLE_PARQUET=TRUE MARCH_SKYLAKE=TRUE swift build -c release

FROM ghcr.io/open-meteo/docker-container-run:latest

RUN useradd --user-group --create-home --system --skel /dev/null --home-dir /app openmeteo
WORKDIR /app

COPY --from=build --chown=openmeteo:openmeteo /build/open-meteo/.build/release/openmeteo-api /app/openmeteo-api
RUN mkdir -p /app/Resources
COPY --from=build --chown=openmeteo:openmeteo /build/open-meteo/.build/release/SwiftTimeZoneLookup_SwiftTimeZoneLookup.resources /app/Resources/SwiftTimeZoneLookup_SwiftTimeZoneLookup.resources
COPY --from=build --chown=openmeteo:openmeteo /build/open-meteo/Public /app/Public

RUN mkdir -p /app/data && chown -R openmeteo:openmeteo /app
VOLUME /app/data

USER openmeteo:openmeteo
ENTRYPOINT ["./openmeteo-api"]
CMD ["serve", "--env", "production", "--hostname", "0.0.0.0", "--port", "8080"]

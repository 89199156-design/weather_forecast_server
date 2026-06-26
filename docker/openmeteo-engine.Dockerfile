FROM ghcr.io/open-meteo/docker-container-build:latest AS build

WORKDIR /build
COPY vendor/open-meteo/Package.swift /build/open-meteo/Package.swift
COPY vendor/open-meteo/Package.resolved /build/open-meteo/Package.resolved

WORKDIR /build/open-meteo
RUN ENABLE_PARQUET=TRUE swift package resolve

COPY vendor/open-meteo /build/open-meteo
RUN ENABLE_PARQUET=TRUE swift package resolve
RUN ENABLE_PARQUET=TRUE MARCH_SKYLAKE=TRUE swift build -c release

FROM ghcr.io/open-meteo/docker-container-run:latest

RUN useradd --user-group --create-home --system --skel /dev/null --home-dir /app openmeteo
WORKDIR /app

COPY --from=build --chown=openmeteo:openmeteo /build/open-meteo/.build/release/openmeteo-api /app/openmeteo-api
RUN mkdir -p /app/Resources
COPY --from=build --chown=openmeteo:openmeteo /build/open-meteo/.build/release/SwiftTimeZoneLookup_SwiftTimeZoneLookup.resources /app/Resources/SwiftTimeZoneLookup_SwiftTimeZoneLookup.resources
COPY --from=build --chown=openmeteo:openmeteo /build/open-meteo/Public /app/Public
COPY --from=build /usr/lib/x86_64-linux-gnu/libarrow*.so* /usr/lib/x86_64-linux-gnu/
COPY --from=build /usr/lib/x86_64-linux-gnu/libparquet*.so* /usr/lib/x86_64-linux-gnu/

RUN mkdir -p /app/data && chown -R openmeteo:openmeteo /app
RUN ldconfig
VOLUME /app/data

USER openmeteo:openmeteo
ENTRYPOINT ["./openmeteo-api"]
CMD ["serve", "--env", "production", "--hostname", "0.0.0.0", "--port", "8080"]

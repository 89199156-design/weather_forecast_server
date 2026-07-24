FROM ghcr.io/open-meteo/docker-container-build@sha256:e0ef0354d44c4a9330eabe68be5b29cf303ca654444db4ae76f2b601ec161e6f AS build

WORKDIR /build/open-meteo
COPY vendor/open-meteo-ecmwf/Package.swift ./Package.swift
COPY vendor/open-meteo-ecmwf/Package.resolved ./Package.resolved
RUN ENABLE_PARQUET=TRUE swift package resolve

COPY vendor/open-meteo-ecmwf/ /build/open-meteo/
COPY vendor/patches/open-meteo-ecmwf-regional.patch /build/open-meteo-regional.patch
RUN git apply --check /build/open-meteo-regional.patch \
    && git apply /build/open-meteo-regional.patch
RUN ENABLE_PARQUET=TRUE swift package resolve
RUN ENABLE_PARQUET=TRUE MARCH_SKYLAKE=TRUE swift build -c release

FROM ghcr.io/open-meteo/docker-container-run@sha256:7e6ee634cc774abdcf1875dc632229d51368a2b32e4714fed880c41bd7155aff

ARG OPENMETEO_UPSTREAM_COMMIT=unknown
ARG ECMWF_PATCH_SHA256=unknown
ARG ECMWF_SOURCE_ID=unknown
LABEL io.weather-forecast.component=ecmwf-native-engine
LABEL io.weather-forecast.openmeteo-upstream-commit=$OPENMETEO_UPSTREAM_COMMIT
LABEL io.weather-forecast.ecmwf-patch-sha256=$ECMWF_PATCH_SHA256
LABEL io.weather-forecast.ecmwf-source-id=$ECMWF_SOURCE_ID

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

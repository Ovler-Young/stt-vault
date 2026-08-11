FROM debian:bookworm-20250520-slim@sha256:364d3f277f79b11fafee2f44e8198054486583d3392e2472eb656d5c780156f5 AS whisper-builder

ARG WHISPER_CPP_COMMIT=306c88f4d1286aec1bf96e544632897886af5501
ARG WHISPER_MODEL_ID=ggml-base.en.bin

RUN rm -f /etc/apt/sources.list \
    && rm -rf /etc/apt/sources.list.d \
    && printf '%s\n' 'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20250601T000000Z bookworm main' > /etc/apt/sources.list \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends ca-certificates=20230311 cmake=3.25.1-1 g++=4:12.2.0-3 git=1:2.39.5-0+deb12u2 make=4.3-4.1 \
    && rm -rf /var/lib/apt/lists/*
RUN git init /src/whisper.cpp \
    && git -C /src/whisper.cpp remote add origin https://github.com/ggml-org/whisper.cpp.git \
    && git -C /src/whisper.cpp fetch --depth=1 origin "$WHISPER_CPP_COMMIT" \
    && test "$(git -C /src/whisper.cpp rev-parse FETCH_HEAD)" = "$WHISPER_CPP_COMMIT" \
    && git -C /src/whisper.cpp checkout --detach "$WHISPER_CPP_COMMIT"
RUN cmake -S /src/whisper.cpp -B /src/whisper.cpp/build -DBUILD_SHARED_LIBS=OFF -DGGML_OPENMP=OFF -DWHISPER_CURL=OFF -DWHISPER_BUILD_SERVER=ON -DGGML_CUDA=OFF -DGGML_METAL=OFF -DCMAKE_EXE_LINKER_FLAGS='-static-libstdc++ -static-libgcc' \
    && cmake --build /src/whisper.cpp/build --target whisper-server -j2

FROM python:3.13.7-slim-bookworm@sha256:781449467ffb6f04218f09b1ecdcdc7d22b289ee5da9ec498b024e24ad7a6db7 AS model-builder

ARG WHISPER_MODEL_ID=ggml-base.en.bin

COPY model-manifest.json build_model.py /tmp/
RUN WHISPER_MODEL_ID="$WHISPER_MODEL_ID" python /tmp/build_model.py /tmp/model-manifest.json /models

FROM python:3.13.7-slim-bookworm@sha256:781449467ffb6f04218f09b1ecdcdc7d22b289ee5da9ec498b024e24ad7a6db7

ARG WHISPER_MODEL_ID=ggml-base.en.bin

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin whisper
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock \
    && rm /tmp/requirements.lock
COPY --from=whisper-builder /src/whisper.cpp/build/bin/whisper-server /usr/local/bin/whisper-server
COPY --from=model-builder --chown=whisper:whisper /models /models
COPY --chown=whisper:whisper src /app/src
COPY --chown=whisper:whisper model-manifest.json /app/model-manifest.json

USER whisper
ENV PYTHONPATH=/app/src WHISPER_MODEL_ID=${WHISPER_MODEL_ID} WHISPER_MODELS_DIR=/models
EXPOSE 8081
ENTRYPOINT ["python", "-m", "mod_whisper_cpu"]

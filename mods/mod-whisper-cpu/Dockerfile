# syntax=docker/dockerfile:1.7
FROM --platform=linux/amd64 golang:1.24.0-alpine3.21@sha256:32ece9b4650da07c5e92744894d2fb9e857729466eeb9e38c99f7ddec83803ec AS gateway-builder

WORKDIR /src/gateway
COPY gateway/go.mod ./
RUN test ! -e go.sum && go mod download
COPY gateway/ ./
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -buildvcs=false -ldflags='-s -w -buildid=' -o /out/gateway ./cmd/gateway

FROM --platform=linux/amd64 alpine:3.22.1@sha256:eafc1edb577d2e9b458664a15f23ea1c370214193226069eb22921169fc7e43f AS apk-packages

ADD --checksum=sha256:83c35935dcc0d592f65bc4626cec243b2c5f1a0a3d3e1361f10c4bb221a941f1 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/acl-libs-2.3.2-r1.apk /packages/acl-libs-2.3.2-r1.apk
ADD --checksum=sha256:d12005fc43aa4e818ce240dcdd5ae90a42a5a4b3ea6937800c7b0a5b877190aa https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/binutils-2.44-r3.apk /packages/binutils-2.44-r3.apk
ADD --checksum=sha256:9e52618457762f65bf273f42d71db8084633a1e9d938ff2611015ba20d44a8cf https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/brotli-libs-1.1.1-r0.apk /packages/brotli-libs-1.1.1-r0.apk
ADD --checksum=sha256:2788ae90dc82999b26d121536ec2bd1c053e46b470c0202229bd4b4d2347d1b4 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/build-base-0.5-r3.apk /packages/build-base-0.5-r3.apk
ADD --checksum=sha256:7cc34c2dda2520b456ab4a55abc37fe245ac90f98ab9f1c7a9be3aec035df48e https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/busybox-1.37.0-r20.apk /packages/busybox-1.37.0-r20.apk
ADD --checksum=sha256:ad33d8d799ed80eab45bb68d63c14062165d3653f4fb52e73959cd31d032210e https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/busybox-binsh-1.37.0-r20.apk /packages/busybox-binsh-1.37.0-r20.apk
ADD --checksum=sha256:9b23aa07901ed64bde0be7ec544f8d2b29de80d996277dc861218c54fff8bc97 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/c-ares-1.34.9-r0.apk /packages/c-ares-1.34.9-r0.apk
ADD --checksum=sha256:8dd7538699961927a36327d622b3b9e9f5c944fd7d486a0cb8b6b833dc74cfbf https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/ca-certificates-20261020-r0.apk /packages/ca-certificates-20261020-r0.apk
ADD --checksum=sha256:d35216a0a9adde79031df246759bad03f235cf201bf0aff8436b88bff59594eb https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/ca-certificates-bundle-20261020-r0.apk /packages/ca-certificates-bundle-20261020-r0.apk
ADD --checksum=sha256:3f99fc6bb19b8e41f33764bf641bb181d57bdd1afb63f2dfee8d4547e1362341 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/cmake-3.31.7-r1.apk /packages/cmake-3.31.7-r1.apk
ADD --checksum=sha256:3e782736df7039c2b8da2ff9bb4c79a67aee9520561c63e746b1c22ebb0938fb https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/curl-8.14.1-r4.apk /packages/curl-8.14.1-r4.apk
ADD --checksum=sha256:94c99dfe7cde72e07aa2b289674e682e821518bfc59e31ce628990d939caed7c https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/file-5.46-r2.apk /packages/file-5.46-r2.apk
ADD --checksum=sha256:544a27bd0e002f54677109e2cce594f10f7d0df5321e5e64604e896bba92cdf8 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/fortify-headers-1.1-r5.apk /packages/fortify-headers-1.1-r5.apk
ADD --checksum=sha256:4490ba28b819efdb26a78493e63942605a022d407652eea77027eaba918e6f1a https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/g++-14.2.0-r6.apk /packages/g++-14.2.0-r6.apk
ADD --checksum=sha256:4fd10bd05784f596e5ff0adc724a94b71380fac0ac276bd257dd861e8b5822e6 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/gcc-14.2.0-r6.apk /packages/gcc-14.2.0-r6.apk
ADD --checksum=sha256:d3f987ae3836ac7774324bff443dd49d03b846209660729d0c30dfff5546e138 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/gmp-6.3.0-r3.apk /packages/gmp-6.3.0-r3.apk
ADD --checksum=sha256:ecd11e4446097ff62b49e6ab8f552b438a304634a8f8e2db713fcbbe62f04c61 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/isl26-0.26-r1.apk /packages/isl26-0.26-r1.apk
ADD --checksum=sha256:7fde81421482507163410715a7ef7d7df6a091870edab855c24e8f6fc84e3e2d https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/jansson-2.14.1-r0.apk /packages/jansson-2.14.1-r0.apk
ADD --checksum=sha256:dc73a46f8a2b0827fb6b0590f07d8f456a41f3fae44e34fe7e3a09b4329325e3 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/jq-1.8.2-r0.apk /packages/jq-1.8.2-r0.apk
ADD --checksum=sha256:b172d4ed8bc4a9e0c9aa9db9086ceeafba9ad887e1d81447dc8c584dbf74d72c https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/libarchive-3.8.11-r0.apk /packages/libarchive-3.8.11-r0.apk
ADD --checksum=sha256:e9a884f8a63f614b722dd5191ede154a6b6b81d99457af610bc6f544a6d2d7bc https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/libatomic-14.2.0-r6.apk /packages/libatomic-14.2.0-r6.apk
ADD --checksum=sha256:0c98810f9332bf1bb1b0acc64eaac4b101036689bffba9ea621416b4b3c190cd https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/libbz2-1.0.8-r6.apk /packages/libbz2-1.0.8-r6.apk
ADD --checksum=sha256:3f5825ced5fde1c66a5376a3f6076b3c08538ad52d9ef23f069857e212d9175a https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/libcrypto3-3.5.9-r0.apk /packages/libcrypto3-3.5.9-r0.apk
ADD --checksum=sha256:3887f3b2e66b513cb0f8211af6369a0ffaf87270c05ed1f18f7313eddc6b3660 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/libcurl-8.14.1-r4.apk /packages/libcurl-8.14.1-r4.apk
ADD --checksum=sha256:8ff83a897b5393832edbd2a7500c37bab685a17d7821925d95460f01747dbac2 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/libexpat-2.10.0-r0.apk /packages/libexpat-2.10.0-r0.apk
ADD --checksum=sha256:04f3467bc967e705221a843fe4d3de5850db826e571686e0c0ed453d38cb5c59 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/libgcc-14.2.0-r6.apk /packages/libgcc-14.2.0-r6.apk
ADD --checksum=sha256:026e5c9d0bab7e82c42fa11b96ed95e66d2549bd5de5c869c18a3600c2e3aa02 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/libgomp-14.2.0-r6.apk /packages/libgomp-14.2.0-r6.apk
ADD --checksum=sha256:515cfe061176c7456ea3548651d0014084d77dd3774b78e2c944404ae75a41ff https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/libidn2-2.3.7-r0.apk /packages/libidn2-2.3.7-r0.apk
ADD --checksum=sha256:1fe23639567b42d215d860b2983ed4ccad442c658fbc9082e946823e87c251a8 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/libmagic-5.46-r2.apk /packages/libmagic-5.46-r2.apk
ADD --checksum=sha256:1e79da4fa1364b7153b8950a363a6de0bef1d1b48d46fcb3b850cd4f233727b2 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/libpsl-0.21.5-r3.apk /packages/libpsl-0.21.5-r3.apk
ADD --checksum=sha256:6801c740b9760b5b08da8db6367ebc18b2cc4dbaf371f7148429538b8bb04e23 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/libssl3-3.5.9-r0.apk /packages/libssl3-3.5.9-r0.apk
ADD --checksum=sha256:939f7c99898f3e8154207a17f4acbe8bc40437e1bb1b43f5525620ca9e452a2e https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/libstdc++-14.2.0-r6.apk /packages/libstdc++-14.2.0-r6.apk
ADD --checksum=sha256:13e2169b46a24f03c87caf91387312724b01951c222bd061d6d87c48903836a9 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/libstdc++-dev-14.2.0-r6.apk /packages/libstdc++-dev-14.2.0-r6.apk
ADD --checksum=sha256:989640e58f7646c0495c3950df36eb7b4df152d8150c2c888f8ab954fed8c908 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/libunistring-1.3-r0.apk /packages/libunistring-1.3-r0.apk
ADD --checksum=sha256:6cd879561ba6380c5664c221c0de33af177becb0d94a5de0d5d90589a2089737 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/libuv-1.51.0-r0.apk /packages/libuv-1.51.0-r0.apk
ADD --checksum=sha256:b0d7184f0e8d926961b82dff3d8a6a1f85db100dfc97e3fa1e6a25bbe9fd0f71 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/linux-headers-6.14.2-r0.apk /packages/linux-headers-6.14.2-r0.apk
ADD --checksum=sha256:b4f393b9d8ce5a431788b31c899734f84d60a47487a6b833ee8d8012c501fe3f https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/lz4-libs-1.10.0-r0.apk /packages/lz4-libs-1.10.0-r0.apk
ADD --checksum=sha256:5b66eb4c2db0b0452f1c88d19909397f09e72425d1d9769a480d6370d8a9c310 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/make-4.4.1-r3.apk /packages/make-4.4.1-r3.apk
ADD --checksum=sha256:bfc0d8bd7e3b8de2fdfcf4977437281e58147cc568194cb82ba887bd190376e6 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/mpc1-1.3.1-r1.apk /packages/mpc1-1.3.1-r1.apk
ADD --checksum=sha256:33f2f274333164dfc25a0002ca58bb99f6d26843d5486c03e3fa207e5432ac59 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/mpfr4-4.2.1_p1-r0.apk /packages/mpfr4-4.2.1_p1-r0.apk
ADD --checksum=sha256:359890bc7c4dc5fee7a85192de60a471c987e4664767288578beca60a44db2c8 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/musl-1.2.5-r13.apk /packages/musl-1.2.5-r13.apk
ADD --checksum=sha256:2ca496dd68c0b340b50cf9940bc2f3312eac8b56ec2d3284a621be6f0c09ffb1 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/musl-dev-1.2.5-r13.apk /packages/musl-dev-1.2.5-r13.apk
ADD --checksum=sha256:d6ee515d30d703e94b55ef9c0a02aea053313f654acbd2c655d18e3907ecb66a https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/nghttp2-libs-1.69.0-r0.apk /packages/nghttp2-libs-1.69.0-r0.apk
ADD --checksum=sha256:52f7f6f99840f252d611ded9d72c8ecfdc99888391d6cbc33a7ba6703c271dfe https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/oniguruma-6.9.10-r0.apk /packages/oniguruma-6.9.10-r0.apk
ADD --checksum=sha256:f34098dde672a06d7b601c12d2e585bbf473292dd08d7e0a81e9ad27962196a6 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/patch-2.8-r0.apk /packages/patch-2.8-r0.apk
ADD --checksum=sha256:c48bd5cf228e606e961f07a67bf08cfae60136cf93f86f5005678ea7b374bfb9 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/rhash-libs-1.4.5-r0.apk /packages/rhash-libs-1.4.5-r0.apk
ADD --checksum=sha256:11b2a5f91caf8eb5daa9a0bc2586f93545de62897f3a704fd49533e1654b7a68 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/ssl_client-1.37.0-r20.apk /packages/ssl_client-1.37.0-r20.apk
ADD --checksum=sha256:37e169b7b400304fa7027cab79f9c1c7a280e83acf6c95463384b8abb7ebb254 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/xz-libs-5.8.4-r0.apk /packages/xz-libs-5.8.4-r0.apk
ADD --checksum=sha256:af24e502645e2b392cae833856d776691ac21655362e5db69d7b89f147f76216 https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/zlib-1.3.2-r1.apk /packages/zlib-1.3.2-r1.apk
ADD --checksum=sha256:1bdd6e57cfbfbfd6e8481cad37ddd5d199950715bec1879b3afb600272dbb09e https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/zstd-libs-1.5.7-r0.apk /packages/zstd-libs-1.5.7-r0.apk

FROM --platform=linux/amd64 alpine:3.22.1@sha256:eafc1edb577d2e9b458664a15f23ea1c370214193226069eb22921169fc7e43f AS whisper-builder

ARG WHISPER_CPP_COMMIT=306c88f4d1286aec1bf96e544632897886af5501
COPY --from=apk-packages /packages /var/cache/apk
RUN apk add --no-network /var/cache/apk/*.apk
RUN curl --fail --location --retry 3 --output /tmp/whisper.tar.gz "https://github.com/ggml-org/whisper.cpp/archive/${WHISPER_CPP_COMMIT}.tar.gz" \
    && mkdir /src \
    && tar -xzf /tmp/whisper.tar.gz -C /src --strip-components=1 \
    && test -f /src/CMakeLists.txt
RUN cmake -S /src -B /src/build \
      -DBUILD_SHARED_LIBS=OFF -DGGML_OPENMP=OFF -DWHISPER_CURL=OFF -DWHISPER_BUILD_SERVER=ON \
      -DGGML_CUDA=OFF -DGGML_METAL=OFF -DGGML_BLAS=OFF -DGGML_LLAMAFILE=OFF \
      -DCMAKE_EXE_LINKER_FLAGS='-static -s' \
    && cmake --build /src/build --target whisper-server -j2 \
    && test -x /src/build/bin/whisper-server \
    && ! readelf -l /src/build/bin/whisper-server | grep -q 'Requesting program interpreter' \
    && ! readelf -d /src/build/bin/whisper-server | grep -q NEEDED \
    && ! strings /src/build/bin/whisper-server | grep -qi libgomp

FROM --platform=linux/amd64 alpine:3.22.1@sha256:eafc1edb577d2e9b458664a15f23ea1c370214193226069eb22921169fc7e43f AS model-builder

ARG WHISPER_MODEL_ID=ggml-base.en.bin
COPY --from=apk-packages /packages /var/cache/apk
RUN apk add --no-network /var/cache/apk/*.apk
COPY model-manifest.json build-model.sh /tmp/
RUN chmod 0555 /tmp/build-model.sh \
    && WHISPER_MODEL_ID="$WHISPER_MODEL_ID" /tmp/build-model.sh /tmp/model-manifest.json /models

FROM scratch

ARG WHISPER_MODEL_ID=ggml-base.en.bin
COPY --from=gateway-builder /out/gateway /gateway
COPY --from=whisper-builder /src/build/bin/whisper-server /whisper-server
COPY --from=model-builder /models/${WHISPER_MODEL_ID} /models/${WHISPER_MODEL_ID}
COPY model-manifest.json /model-manifest.json

USER 10001:10001
ENV WHISPER_MODEL_ID=${WHISPER_MODEL_ID} WHISPER_MANIFEST_PATH=/model-manifest.json WHISPER_MODELS_DIR=/models PATH=/
EXPOSE 8081
HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=30s CMD ["/gateway", "healthcheck"]
ENTRYPOINT ["/gateway"]

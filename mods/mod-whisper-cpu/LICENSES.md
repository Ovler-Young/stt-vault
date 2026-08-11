# License and Checksum Record

`whisper.cpp` is pinned to commit `306c88f4d1286aec1bf96e544632897886af5501`
from `https://github.com/ggml-org/whisper.cpp`, licensed under MIT.

The selected `ggml-base.en.bin` artifact is pinned to Hugging Face revision
`5359861c739e955e79d9a303bcbc70fb988958b1` and SHA-256
`a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002`.
Its source repository declares MIT and public access. All supported candidates
and their checksums are in `model-manifest.json`. The selected-model image
downloads exactly its `WHISPER_MODEL_ID` artifact during its Docker build,
verifies the manifest SHA-256, and contains no runtime model download path.

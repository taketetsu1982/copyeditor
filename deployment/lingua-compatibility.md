# Local language detector dependency gate

`lingua-language-detector==2.2.0` is pinned for CPython 3.12. This dependency
is not yet connected to the public language resolver. Existing runtime behavior
is unchanged. Task 153 must retain all 75 languages, including languages whose
editing rules are not installed, rather than forcing them into English.

## Distribution and API

The [official release](https://pypi.org/project/lingua-language-detector/2.2.0/)
and [upstream implementation](https://github.com/pemistahl/lingua-py) provide
native wheels with bundled statistical language models. No runtime model download
is needed. The installed wheel includes the Apache-2.0 license; retain its license
in redistributed images. This adds an Apache-2.0 dependency, not a change to the
repository's MIT license.

Verified CPython 3.12 wheels (SHA-256 from the release metadata):

| Platform suffix | SHA-256 |
| --- | --- |
| `macosx_11_0_arm64` | `2fe367f7c112a0445218407e259338a88af770d5c84a550c20ebe11d5053f03d` |
| `manylinux_2_17_aarch64.manylinux2014_aarch64` | `9ac7453c08ab9699706a92f15480ae3d4b66761c15e1577a1ba31d1635780f3a` |
| `manylinux_2_17_x86_64.manylinux2014_x86_64` | `63d99c7570ba09525f1702e4e4b2362f8f1f7e0a0fba93a3a53d3f322e00659d` |

The filename prefix is `lingua_language_detector-2.2.0-cp312-cp312-`.
The tested Docker targets are glibc Linux arm64 and amd64, not Alpine/musl.
`LanguageDetectorBuilder.from_all_languages().build()` supplies
`detect_language_of(text)` and `compute_language_confidence_values(text)`.
Each confidence entry exposes `language.iso_code_639_1.name` and `value`.
The ISO name is uppercase; the future resolver must lowercase it and compare
exactly with installed rule IDs. Empty, numeric and punctuation-only input
returns `None` from detection and zero confidence values. Confidence ordering
among ties is unspecified. Repeated scores can vary at floating-point roundoff
scale; the test requires stable language and gap decisions, and score agreement
within relative 1e-12 / absolute 1e-15. It does not round the production gap.

## Resource envelope and evidence

Owner confirmation: pending (to be confirmed during PR review).

The proposed dependency-process envelope is cold import/preload under 30 seconds,
12,031-character detection under 5 seconds, and peak RSS below 512 MiB.
The owner must confirm these limits and the deployment environment during PR
review before Task 153 proceeds. This is not a claim that the entire server fits
512 MiB; reserve additional memory for its existing providers and request handling.

The probe preloads all languages and exercises Japanese, English, Chinese,
German (no installed rule), three non-language inputs, repeated classifications,
and the 12,000-body-character limit plus up to 31 item separators. It does not
establish general detector accuracy or native-language acceptance.

Measured on 2026-09-22 with Python 3.12; amd64 runs under Docker Desktop emulation:

| Environment | Cold/preload s | Maximum body s | Peak RSS MiB |
| --- | ---: | ---: | ---: |
| macOS arm64 / Python 3.12.14 | 0.0040 | 0.0050 | 69.39 |
| Linux arm64 / python:3.12-slim | 0.0085 | 0.0059 | 178.26 |
| Linux amd64 / python:3.12-slim | 0.0430 | 0.0099 | 189.18 |

Both Linux probes installed the local wheel with `pip --no-index --no-deps`
inside `docker run --rm --network none`; no model endpoint was available.
The unit probe also poisons Python sockets. Docker network isolation covers native
extension networking that Python monkeypatching alone cannot prohibit.
Reproduce locally with `python -m pytest tests/unit/test_lingua_dependency.py -q -s`.
For either Docker architecture, mount the wheel directory and this test file
read-only, install the matching wheel without an index, and invoke its two test
functions with `runpy` (pytest is unnecessary inside the container).

If a target lacks a wheel, exceeds the approved resource envelope, or cannot run
offline, stop Task 153 and return the evidence for a specification decision.
An alternative candidate is the offline statistical `langid` implementation;
its language coverage, confidence semantics and license require separate review.
Do not silently substitute it or restrict the language set to pass this gate.

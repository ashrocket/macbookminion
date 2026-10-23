# Runtime maintenance on the existing Monterey worker

Reviewed **2026-10-23** against the primary sources linked below. This is a
concrete selection and verification plan for the one existing Intel Mac, not a
record of installation. No large installer or runtime archive was downloaded for
this review. Release metadata and the small Python Sigstore bundle were inspected.

| Component | Feasible before an OS upgrade? | Recommended scope |
| --- | --- | --- |
| Python 3.14.9 | Yes: official Intel-compatible package meets Monterey's OS version. | Install alongside the existing interpreter, then create and test a new environment. The package requires administrator installation. |
| Node 22.23.4 | Binary compatibility candidate: official x64 target is macOS 11+. | Extract the signed-checksum-verified archive into a dedicated user-owned version directory; test explicitly. Host security/support remains a separate constraint. |
| Ruby 3.4.12 or 4.0.9 | Possible source-build route; no validated portable binary established here. | Prefer 3.4.12 for the initial compatibility trial, with an explicit maintained OpenSSL dependency and a user-owned prefix. Compilation is pending. |
| Node 24 | No on unchanged Monterey: official target is macOS 13.5+. | Revisit after attended OS maintenance. |
| Current official PyTorch | No current Intel macOS wheel established. | Keep the current Mac's macOS role; separately evaluate Linux on this same hardware if this workload warrants an OS migration. Do not invent another available worker. |
| macOS through OCLP | Requires attended maintenance and recovery preparation. | Follow the [Intel worker guide](intel-workers.md); no automatic patching, SIP change or reboot. |

## Python: exact official artifact

The [Python 3.14.9 release page](https://www.python.org/downloads/release/python-3149/)
identifies an October 21 security release and a 72.3 MB installer for macOS 10.15
and later. Its filename contains `macos11`; the release table, rather than that
filename, provides the published minimum. Python's [macOS guide](https://docs.python.org/3.14/using/mac.html)
describes universal2 Intel/Apple Silicon installers and PSF signing/notarization.

- Artifact: [python-3.14.9-macos11.pkg](https://www.python.org/ftp/python/3.14.9/python-3.14.9-macos11.pkg)
- SHA-256: `08e312f20844b524c2f8c5e5b2e7f49608c22e8c7cb194f5834d385c94383462`
- [Sigstore bundle](https://www.python.org/ftp/python/3.14.9/python-3.14.9-macos11.pkg.sigstore)

The [official verification guidance](https://www.python.org/downloads/metadata/sigstore/)
specifies `hugo@python.org` and issuer `https://github.com/login/oauth` for 3.14.
The fetched bundle's certificate names that email and its digest matches the
published SHA-256. Inspecting metadata is **not cryptographic verification** of the
package. After a permitted download, verify all of the following before installing:

```sh
shasum -a 256 python-3.14.9-macos11.pkg
pkgutil --check-signature python-3.14.9-macos11.pkg
spctl --assess --type install --verbose=2 python-3.14.9-macos11.pkg
python3 -m sigstore verify identity \
  --bundle python-3.14.9-macos11.pkg.sigstore \
  --cert-identity hugo@python.org \
  --cert-oidc-issuer https://github.com/login/oauth \
  python-3.14.9-macos11.pkg
```

Use an already trusted verifier or provision it as a separate reviewed dependency;
the commands above do not install one. Require a trusted Apple installer signature
identifying Python Software Foundation, a successful macOS assessment, the exact
hash, and successful Sigstore verification. Do not dismiss a failed check or
invent an expected certificate Team ID. Python 3.14 uses Sigstore instead of the
older release PGP scheme.

Use the package's normal installer on the **worker**, reviewing Customize options
before any system-wide path links or shell-profile changes. Keep the existing
interpreter and invoke the new framework executable explicitly. Complete the
documented certificate setup and inspect the resulting TLS library and trust
configuration; a new Python version does not update every existing virtualenv.

```sh
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14 -I -c \
  'import platform, ssl; print(platform.python_version(), platform.machine(), ssl.OPENSSL_VERSION)'
```

Then create a new task environment, install its reviewed dependencies and run a
small HTTPS request with certificate validation plus the relevant job test. Do
not replace `/usr/bin/python3`. Switch a job only after successful worker-side
verification; keep the prior executable path available for rollback.

## Node: a compatible 22.x route, with an OS support caveat

[Node 22.23.4](https://nodejs.org/en/blog/release/v22.23.4) is the security release
identified by the current 22.x download metadata. Its [platform document](https://github.com/nodejs/node/blob/v22.x/BUILDING.md)
targets macOS 11+ for Intel binaries, so Monterey meets the binary minimum.
The same document explicitly excludes vendor-EOL operating systems from support,
regardless of the table. Therefore “runs on Monterey” cannot be reported as
“fully supported and securely patched host.” This does not resolve the OS gap.

- Archive: [node-v22.23.4-darwin-x64.tar.gz](https://nodejs.org/dist/v22.23.4/node-v22.23.4-darwin-x64.tar.gz)
- SHA-256: `efcc2b0db6f94bf53eddb038fe2b370277a042bd29e4f19b8f8e23598795b819`
- Official [checksum manifest](https://nodejs.org/dist/v22.23.4/SHASUMS256.txt) and [signed manifest](https://nodejs.org/dist/v22.23.4/SHASUMS256.txt.asc)

Follow Node's [binary-verification procedure](https://github.com/nodejs/node#verifying-binaries)
using independently authenticated release keys. A checksum fetched from the same
server is useful integrity evidence, but should not substitute for release
signature verification. Extract only after verification into a new directory such
as `~/.local/opt/node-v22.23.4-darwin-x64` on the worker. Do not replace an existing
directory or change login profiles automatically. Point the approved job's PATH
at that version's `bin` directory, clear unexpected `NODE_OPTIONS`, and check
`node --version`, `process.arch`, a TLS request, and the project's test. Switching
back to the old explicit directory is the runtime rollback.

Node 24 remains a later target: [its x64 binary minimum](https://github.com/nodejs/node/blob/v24.x/BUILDING.md)
is macOS 13.5. Installing an old Node 20 to avoid that minimum would discard the
maintained release line rather than solve the platform problem.

## Ruby: build a maintained stack, not just a newer executable

[Ruby downloads](https://www.ruby-lang.org/en/downloads/) currently offers 4.0.9
and 3.4.12 source releases. Both branches remain maintained. A standalone
[ruby-build](https://github.com/rbenv/ruby-build) installation can target a private
prefix without modifying Apple's Ruby or adding a version manager to shell startup.
This is a plausible Monterey build route, **not a proven build on this worker**.

Use the official [Ruby 3.4.12 source archive](https://cache.ruby-lang.org/pub/ruby/3.4/ruby-3.4.12.tar.gz)
with SHA-256 `5e5d85ad93b0adcccbad47733680f4892bd0078e3342437f7babce01c1872479`
for an initial project-compatible trial. Ruby 4.0.9 is an alternative if its
application compatibility is established. Do not upgrade the language major
version solely because the download page labels it newest.

There is a dependency trap: the inspected [3.4.12 recipe](https://github.com/rbenv/ruby-build/blob/master/share/ruby-build/3.4.12)
can build OpenSSL 3.0.22 when it does not find a qualifying installed library.
[OpenSSL's policy](https://openssl-library.org/policies/releasestrat/) says 3.0 is
out of public support. Do not treat the recipe default as a patched dependency
guarantee. [OpenSSL downloads](https://openssl-library.org/source/) lists 3.5.9 on
the maintained 3.5 LTS line; review and verify its official signature/hash, build
it in a separate user-owned prefix, and explicitly link Ruby to that prefix.

Before compiling, inventory the worker's compiler/SDK and required libraries.
[ruby-build's build-environment guide](https://github.com/rbenv/ruby-build/wiki)
identifies OpenSSL, libyaml and other development dependencies. Plan these
individually; Intel Homebrew's limited support does not disappear because a recipe
calls `brew`. A MacPorts installation is an alternative package-management project,
not an already-available portable runtime: its fetched [ruby40 listing](https://ports.macports.org/port/ruby40/)
showed 4.0.7, behind the upstream release at review.

Compile through the shared queue with modest parallelism, reviewed source checksums,
and paths owned by the dedicated worker account. An illustrative invocation after
dependencies and a reviewed ruby-build revision are present is:

```sh
RUBY_CONFIGURE_OPTS="--with-openssl-dir=$HOME/.local/opt/openssl-3.5.9 --disable-yjit" \
  MAKE_OPTS=-j2 ruby-build 3.4.12 "$HOME/.local/opt/ruby-3.4.12"
"$HOME/.local/opt/ruby-3.4.12/bin/ruby" -ropenssl -rpsych -rzlib -e \
  'puts RUBY_DESCRIPTION; puts OpenSSL::OPENSSL_VERSION; puts OpenSSL::OPENSSL_LIBRARY_VERSION; puts Psych::VERSION; puts Zlib::ZLIB_VERSION'
```

The paths illustrate the intended isolated layout; they are not evidence those
dependencies exist. Inspect dynamic links, test HTTPS trust and the actual Ruby
workload, and retain its build log. Do not pass the build if OpenSSL, YAML or zlib
extensions are missing. The owner decides whether this compilation effort is
worth doing before the attended OS upgrade.

## What remains attended

Updating Python or extracting Node does not authorize bootloader or OS work. OCLP
maintenance needs the exact target build, independent backup, tested recovery media
and physical access described in [intel-workers.md](intel-workers.md). There is
only one known Intel worker, so take it out of dispatch during that maintenance;
do not plan on another available machine taking over. Current PyTorch on Intel
still requires a separately chosen supported OS/runtime route.

## Repository dependency observation

The official [actions/checkout latest release](https://github.com/actions/checkout/releases/tag/v7.0.1)
was v7.0.1. GitHub's [tag API](https://api.github.com/repos/actions/checkout/git/ref/tags/v7.0.1)
resolved it to commit `3d3c42e5aac5ba805825da76410c181273ba90b1`.
The checkout [README](https://github.com/actions/checkout/blob/v7.0.1/README.md)
documents Node 24 and runner requirements inherited from v5, plus safer fork-PR
handling in v7. Review the workflow triggers and use a compatible hosted runner
before updating the pin. This is controller CI maintenance, separate from the
Monterey worker's installed Node. No workflow file was changed by this review.

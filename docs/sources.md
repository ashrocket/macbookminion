# Primary-source research record

Fetched **2026-10-23**. These are upstream observations on that date, not evidence
that a worker was installed, upgraded or tested. Recheck before selecting versions.
Where a website's summary and its release artifacts differ, prefer the exact
release/platform evidence and retain the discrepancy in the decision record.

| Source | Relevant observation |
| --- | --- |
| [OCLP supported models](https://dortania.github.io/OpenCore-Legacy-Patcher/MODELS.html) | MacBookPro11,4 is listed; the project recommends latest native firmware first. Hardware-specific caveats remain. |
| [OCLP releases](https://github.com/dortania/OpenCore-Legacy-Patcher/releases) | 2.5.1 is labelled Latest; 3.0.0-rc.3 is explicitly prerelease and adds Tahoe support with instability/data-loss cautions. |
| [OCLP repository](https://github.com/dortania/OpenCore-Legacy-Patcher) | README mentions Big Sur through Tahoe; that does not override the release candidate status. |
| [OCLP FAQ](https://dortania.github.io/OpenCore-Legacy-Patcher/FAQ.html) | Still describes Big Sur through Sequoia targets; says macOS downgrade requires erase/recovery rather than an in-place downgrade. |
| [OCLP post-install guide](https://dortania.github.io/OpenCore-Legacy-Patcher/POST-INSTALL.html) | Root patches restore legacy device functionality and need reapplication after OS updates; reduced SIP requirements matter. |
| [OCLP update guide](https://dortania.github.io/OpenCore-Legacy-Patcher/UPDATE.html) | Separates app, bootloader and root patch versions; documents two specific read-only NVRAM version queries. |
| [Python lifecycle](https://devguide.python.org/versions/) | 3.15/3.14 bugfix; 3.13/3.12/3.11 security; 3.10 EOL. |
| [Python macOS downloads](https://www.python.org/downloads/macos/) | Lists 3.15.1, 3.14.9, 3.13.17 releases dated October 21, 2026. |
| [Python 3.15 macOS documentation](https://docs.python.org/3.15/using/mac.html) | Universal2 includes Intel; installer minimum OS and signatures must be inspected for the selected artifact. |
| [Node release status](https://nodejs.org/en/about/previous-releases) | 24 and 22 LTS; 26 Current; 20 EOL. |
| [Node 24 build/platform requirements](https://github.com/nodejs/node/blob/v24.x/BUILDING.md) | Official macOS x64 platform minimum is 13.5. |
| [Node October security release](https://nodejs.org/en/blog/vulnerability/october-2026-security-releases) | Security updates exist for 26.x, 24.x and 22.x; a major version alone is insufficient patch evidence. |
| [Ruby maintenance branches](https://www.ruby-lang.org/en/downloads/branches/) | 4.0/3.4 normal maintenance; 3.3 security; 3.2 EOL. |
| [Ruby downloads](https://www.ruby-lang.org/en/downloads/) | Stable release identified as 4.0.9; macOS installation uses third-party tooling. |
| [PyTorch macOS x86 deprecation](https://github.com/pytorch/pytorch/issues/114602) | Upstream discussion and 2.3 milestone document withdrawal of official x86 macOS CI/binaries; community source builds are a different support path. |
| [PyTorch official CPU wheel index](https://download.pytorch.org/whl/cpu/torch/) | 2.14.1 has Linux x86_64 CPU wheels and macOS arm64 wheels. Historical 2.2.2 Intel macOS wheels remain downloadable; availability does not make them current. |
| [PyTorch local install page](https://pytorch.org/get-started/locally/) | Fetched page contains a stale-looking 2.7.0 selector while the official wheel index contains newer versions. Do not use this rendered selector alone to claim latest patch status. |
| [Homebrew support tiers](https://docs.brew.sh/Support-Tiers) | Intel macOS is Tier 3; new Intel bottles have stopped. The page announces removal of Intel execution support in or after September 2027. |

The runtime choices in [intel-workers.md](intel-workers.md) are recommendations
inferred from these facts. The proposed Linux route has not been live-validated
by this documentation work. No upstream page guarantees that this particular
OCLP configuration, package combination or browser workload is secure.

During live HTTP verification, the worker's Python trust store failed certificate
verification. An optional explicit CA bundle was prepared from
[certifi 2026.10.23 on PyPI](https://pypi.org/project/certifi/2026.10.23/).
The downloaded wheel SHA-256 was verified as
`e9b54de76e530bb6cf0ccd036787103acb58fd42ba930ea8bf948ddbaa2f61d1`;
the extracted `cacert.pem` hash was
`2ef52ad56b4803e264045c915e55c236cbfa045b84b81efefc218a19eb44bd2b`.
TLS chain and hostname checks remain enabled. This updates the research job's
certificate trust input, not the worker's OS, Python or OpenSSL installation.

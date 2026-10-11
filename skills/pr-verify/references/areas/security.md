# Security

- Outbound requests: SSRF to private, loopback, link-local and metadata addresses, directly and via redirects. Check DNS rebinding (resolve-then-connect) and non-HTTP schemes.
- Injection: SQL, shell (`shell=True`, string-built commands), template, LDAP, header (CRLF) and log injection. Option injection when user input becomes a CLI argument (a value starting with `-`).
- Files: path traversal (`../`, absolute paths, symlinks), zip-slip, upload type and size, content sniffing, and where uploaded files are served from.
- Deserialization: `pickle`, `yaml.load`, `eval`, `Function`. Untrusted JSON with prototype keys.
- Output: XSS through `innerHTML`, unsanitized markdown and SVG, attribute injection. Check CSP still applies.
- Secrets: none in the code, logs, errors, URLs or client bundles. Rotation still works.
- Crypto: no homemade crypto, constant-time comparison for secrets, randomness from a CSPRNG, correct key and IV handling.
- Information leaks: different errors or timing for "exists" vs "forbidden", verbose stack traces, internal hostnames or versions.
- Abuse: rate limits, cost amplification (one request triggering unbounded work), resource exhaustion through large inputs.

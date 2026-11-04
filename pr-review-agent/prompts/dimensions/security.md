# Dimension: Security

Focus: can an attacker, a malicious input, or a careless deploy exploit what this PR adds?

## Checklist

- **Injection:** SQL built with string formatting/concatenation, shell commands
  with interpolated input (`shell=True`, backticks, `os.system`), template
  injection, LDAP/XPath/NoSQL injection, `eval`/`exec`/`pickle.loads` on input.
- **AuthN / AuthZ:** new endpoints or handlers without auth, missing ownership
  checks (IDOR: `/orders/{id}` returning anyone's order), privilege checks done
  client-side only, auth bypass on error paths.
- **Secrets:** API keys, tokens, passwords, private keys or connection strings
  committed in code, config, tests, notebooks, or Terraform; secrets logged or
  returned in error messages.
- **Web:** XSS (`innerHTML`, `dangerouslySetInnerHTML`, unescaped templates),
  CSRF on state-changing routes, open redirects, permissive CORS (`*` with
  credentials), missing security headers when the PR touches them.
- **Files & paths:** path traversal (`../` in user-supplied filenames), unsafe
  uploads, insecure temp files, overly broad file permissions.
- **Network / SSRF:** fetching user-supplied URLs, disabled TLS verification
  (`verify=False`), plaintext protocols for sensitive data.
- **Crypto:** weak hashes for passwords (md5/sha1/unsalted), custom crypto,
  predictable randomness (`random` for tokens).
- **Infrastructure as code:** public S3 buckets, `0.0.0.0/0` ingress on admin
  ports, IAM `*` actions/resources, unencrypted storage, secrets in tfvars.
- **Dependencies:** newly added packages that are unpinned, typosquat-looking, or
  known-vulnerable.

## How to work

1. Trace every new source of external input (HTTP params, files, env, queue
   messages, DB values written by users) to where it is used.
2. For each sink (query, shell, filesystem, HTML, redirect, outbound request),
   confirm the input is validated, parameterized, or escaped.
3. Report only exploitable or clearly unsafe patterns. Describe the class of
   attack and the affected input; do not write working exploit payloads.

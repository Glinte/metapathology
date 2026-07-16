# scikit-build-core#1482 reproduction

This is a reduced version of
[scikit-build-core#1482](https://github.com/scikit-build/scikit-build-core/issues/1482).
It pins scikit-build-core 1.0.0, the release named in the report. One installed
distribution provides `mqt.core`; the editable distribution provides
`mqt.ddsim` and an install-tree leaf file.

From the repository root on Windows:

```powershell
.\reproductions\scikit-build-core-1482\reproduce.ps1
```

The direct run can import `mqt.ddsim` but fails to import the separately
installed `mqt.core`. The editable install's `ScikitBuildRedirectingFinder`
claims the `mqt` namespace first and supplies a truncated `__path__`, hiding
the other distribution's contribution to that namespace.

The monitored run preserves that result and attributes the `mqt` namespace,
`mqt.ddsim`, and its installed leaf module to
`ScikitBuildRedirectingFinder`. The namespace truncation explains the failed
import. With exact import outcomes enabled, the causal explanation verifies
that the omitted namespace location contains `mqt.core` and links that path to
the failed import boundary. The two expected concrete editable redirects remain
visible as informational findings.

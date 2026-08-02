namespace VRCForge.Editor
{
    // The source tree is intentionally unbound. The release builder replaces
    // these values only in the staged Unity payload after the paired desktop
    // and backend binaries exist. A package made directly from source therefore
    // keeps managed Core lanes closed instead of trusting a mutable sidecar.
    internal static class VRCForgeMcpTrustedRelease
    {
        internal const string DesktopSha256 = "";
        internal const string BackendSha256 = "";
    }
}

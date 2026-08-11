namespace VRCForge.Editor
{
    // The source tree is intentionally unbound. The release builder writes the
    // paired desktop/backend digests into this fixed project asset only after
    // both binaries exist. The verifier reads that asset for every managed
    // connection, so Unity package import ordering cannot leave an older digest
    // compiled into Assembly-CSharp-Editor.dll. A package made directly from
    // source keeps the asset empty and managed Core lanes closed.
    internal static class VRCForgeMcpTrustedRelease
    {
        internal const string AssetPath = "Assets/VRCForge/Editor/MCP/VRCForgeMcpTrustedRelease.json";
    }
}

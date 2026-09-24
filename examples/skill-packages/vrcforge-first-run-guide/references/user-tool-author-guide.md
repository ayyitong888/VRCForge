# Unity user-tool author guide

This guide describes the existing VRCForge user-tool contract. A user tool is an explicitly installed package entrypoint that VRCForge validates, copies into the selected Unity project, and exposes only after the running Core has compiled and discovered it. A Skill is instructions and support content; importing a Skill does not compile or run its C# source.

## Package and descriptor

Create a normal `.vsk` package through the existing Skill package service. The package must be imported and enabled before a Unity-tool install can be prepared. Its manifest declares `entrypoints.unityTools` for the descriptor and a second C# entrypoint whose value is the descriptor's `source` (for example, `entrypoints.unitytoolsource: "AssetNoteCreateTool.cs"`). The descriptor at `unityTools` uses schema `vrcforge.user_unity_tools.v1`:

```json
{
  "schema": "vrcforge.user_unity_tools.v1",
  "tools": [
    {
      "toolId": "asset.note_create",
      "typeName": "Example.UserTools.AssetNoteCreateTool",
      "source": "AssetNoteCreateTool.cs",
      "description": "when-to-use: create one new note TextAsset under the managed generated folder. when-NOT-to-use: do not overwrite existing assets or write outside that folder.",
      "inputSchema": {
        "type": "object",
        "properties": {
          "targetAssetPath": { "type": "string", "description": "New .txt path under Assets/VRCForgeGenerated/ToolNotes/." },
          "note": { "type": "string", "description": "Text to persist in the new TextAsset." }
        },
        "required": ["note", "targetAssetPath"],
        "additionalProperties": false
      }
    }
  ]
}
```

`toolId` is a lowercase identifier. `typeName` must be the exact fully qualified loaded C# type. `source` must be the declared C# entrypoint, and the descriptor schema must describe the same fields as the source's `VRCForgeInput` metadata. Descriptions must include both `when-to-use` and `when-NOT-to-use` so an Agent can select the tool safely.

The smallest useful source shape is a static attributed class with a static `HandleCommand(JObject)` method. The command attribute is discovery metadata, not an execution grant:

```csharp
using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using VRCForge.Core.MCP;

namespace Example.UserTools
{
    [VRCForgeCommand(
        "asset.note_create",
        Summary = "when-to-use: create one new note TextAsset under the managed generated folder. when-NOT-to-use: do not overwrite existing assets or write outside that folder.",
        Access = VRCForgeCommandAccess.RequiresApproval,
        Category = "user")]
    public static class AssetNoteCreateTool
    {
        public sealed class Parameters
        {
            [VRCForgeInput("New .txt path under Assets/VRCForgeGenerated/ToolNotes/.", IsRequired = true)]
            public string targetAssetPath { get; set; } = "";

            [VRCForgeInput("Text to persist in the new TextAsset.", IsRequired = true)]
            public string note { get; set; } = "";
        }

        public static object HandleCommand(JObject parameters)
        {
            var relative = ((string)parameters["targetAssetPath"] ?? "").Replace('\\', '/');
            const string prefix = "Assets/VRCForgeGenerated/ToolNotes/";
            if (!relative.StartsWith(prefix, StringComparison.Ordinal) || !relative.EndsWith(".txt", StringComparison.Ordinal))
                throw new ArgumentException("The target must be a .txt file under the managed note folder.");
            if (relative.Contains("..", StringComparison.Ordinal) || relative.Contains("//", StringComparison.Ordinal))
                throw new ArgumentException("The target path is not normalized.");
            var path = relative;
            var parent = Path.GetDirectoryName(path.Replace('/', Path.DirectorySeparatorChar));
            if (parent == null || !Directory.Exists(parent)) throw new DirectoryNotFoundException(parent);
            if (File.Exists(path) || File.Exists(path + ".meta") || AssetDatabase.LoadMainAssetAtPath(path) != null)
                throw new IOException("The target already exists; refusing overwrite.");
            var note = (string)parameters["note"] ?? "";
            var expected = new UTF8Encoding(false).GetBytes(note);
            var created = false;
            try
            {
                using (var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                { created = true; stream.Write(expected, 0, expected.Length); stream.Flush(true); }
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                var actual = File.ReadAllBytes(path);
                var loaded = AssetDatabase.LoadAssetAtPath<TextAsset>(path);
                var expectedSha = Sha256(expected); var actualSha = Sha256(actual);
                if (loaded == null || loaded.text != note || expectedSha != actualSha)
                    throw new IOException("Disk or independently loaded TextAsset readback did not match.");
                return new JObject {
                    ["schema"] = "vrcforge.user_tool_asset_write.v1", ["ok"] = true, ["verified"] = true,
                    ["mutationStarted"] = true, ["mutationApplied"] = true, ["commitState"] = "committed",
                    ["assetPath"] = path, ["readback"] = new JObject {
                        ["verified"] = true, ["assetPath"] = path, ["sha256"] = actualSha,
                        ["expectedSha256"] = expectedSha, ["text"] = loaded.text,
                        ["type"] = loaded.GetType().FullName
                    }
                };
            }
            catch
            {
                if (created) { AssetDatabase.DeleteAsset(path); if (File.Exists(path)) File.Delete(path); if (File.Exists(path + ".meta")) File.Delete(path + ".meta"); }
                throw;
            }
        }

        private static string Sha256(byte[] bytes)
        {
            using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }
    }
}
```

Generate the descriptor from the compiled source during authoring, before exporting the package. Run this snippet in your authoring code with `Newtonsoft.Json.Linq` and `VRCForge.Core.MCP` imported:

```csharp
var tool = VRCForgeToolRegistry.Describe(typeof(Example.UserTools.AssetNoteCreateTool));
var packageDescriptor = new JObject {
    ["schema"] = "vrcforge.user_unity_tools.v1",
    ["tools"] = new JArray(new JObject {
        ["toolId"] = tool.Name,
        ["typeName"] = tool.ToolType.FullName,
        ["source"] = "AssetNoteCreateTool.cs",
        ["description"] = tool.Description,
        ["inputSchema"] = tool.CreateInputSchema()
    })
};
```

Save `packageDescriptor.ToString()` as the JSON file declared by `entrypoints.unityTools` in your authoring package folder, then export through the existing Skill package service. The JSON above illustrates the generated result; regenerate it after changing the C# metadata instead of maintaining a second schema by hand. The registry includes parameter descriptions, required fields in its stable order, enum values and any `DefaultLiteral` metadata. A C# property initializer alone does not declare a schema default. Existing packages still require their declared schema to match the compiled registry exactly.

Put `VRCForgeInput` attributes on the request object's public fields or properties. Validate values in the handler too. Return structured data or the existing VRCForge result object; do not create a second approval or result protocol. The first user-tool wrapper still requires approval even when a handler describes itself as read-only; the attribute never bypasses Gateway approval.

## Install and readback

The existing path is:

1. Export the source with the existing Skill package service, then import it into the application package store and enable the package. The installed, verified package store is the authority; do not read the original archive during install.
2. Request the existing supervised install for the exact selected project. The install prepares a plan from the verified package, writes under `Assets/VRCForgeUserTools/<packageId>/Editor/`, and returns a `pending_compile` result with verified file readback. It always requires explicit approval and a Unity checkpoint.
3. Refresh the selected Unity project's AssetDatabase through the existing approved refresh path and wait for compilation. A copied source or `pending_compile` response is not readiness.
4. Use the public Gateway read tool `vrcforge_list_user_unity_tools` to inspect the compiled catalog, then use the public approved write tool `vrcforge_invoke_user_unity_tool` with the returned `packageId`, `packageDigest`, `toolId` and object `arguments`. The Core-facing `vrc_list_user_tools` / `vrc_invoke_user_tool` calls are internal implementation details of those Gateway wrappers.

The generated `tool-package.json` uses schema `vrcforge.user_unity_tool_install.v1`. Its tool record includes `source`, `sourceSha256`, `description` and `inputSchema`; its generated `stampType` binds the package digest. The installed source and record are checked again before invocation, so source edits, package digest changes, disabled packages and duplicate or missing loaded types make the tool unavailable.

`vrcforge_list_user_unity_tools` may include a `repairBaseline` object. When available it has `available: true`, `scope: "disk_source_tree"`, `sourcePath: "Assets/VRCForge"`, `coreSourceTreeSha256`, and `loadedImplementationVerified: false`. This is a Gateway disk-source compatibility fingerprint, not a claim that a live loaded assembly has been repaired. If the baseline is unavailable, the catalog reports `available: false` with a reason while ordinary catalog entries remain inspectable. Core version and contract version are read from the existing runtime owner and must be captured into the package's repair declaration at authoring time. A complete repair declaration has all four fields:

```json
"repairs": [
  {
    "toolId": "vrc_get_property",
    "coreVersion": "<runtimeCoreInfo.coreVersion>",
    "toolContractVersion": "<runtimeCoreInfo.toolContractVersion>",
    "coreSourceTreeSha256": "<repairBaseline.coreSourceTreeSha256>"
  }
]
```

Replace every placeholder with the values read from the selected project's current runtime and Gateway baseline before exporting the package. Do not invent a version or omit the baseline hash.

## Ownership boundaries

- Official Core owns the fixed official tool registry, Core installation and Core upgrades.
- User packages own their declared tool source, descriptor and package digest. They cannot add official tools, load arbitrary assemblies, compile Roslyn code at runtime, or bypass the approval/checkpoint path.
- Repair evidence owns the exact disk-source baseline for the selected project. It does not authorize a user tool, silently restore files, or replace the running Core.

When reporting completion, include the approval result, checkpoint/write receipt, refresh/compile result, catalog availability and an independent readback. Do not claim a user tool is ready from package import, copied files, a listening Unity process or a stale catalog alone.

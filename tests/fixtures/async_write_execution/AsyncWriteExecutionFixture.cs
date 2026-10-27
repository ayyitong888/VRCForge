// Run only in a disposable Unity project with the complete Core imported.
// This starts at the accepted in-process queue; it does not exercise TCP peer admission.
using System;
using System.IO;
using System.Reflection;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using VRCForge.Editor;

public static class AsyncWriteExecutionFixture
{
    const BindingFlags Static = BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.Public;
    static readonly Type Server = typeof(VRCForgeMcpCoreServer);
    static object Call(Type type, string method, params object[] arguments)
        => type.GetMethod(method, Static).Invoke(null, arguments);
    static void Set(object value, string field, object data)
        => value.GetType().GetField(field, BindingFlags.Instance | BindingFlags.Public).SetValue(value, data);
    static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }

    static Task NextEditorUpdate()
    {
        var completion = new TaskCompletionSource<bool>();
        EditorApplication.CallbackFunction callback = null;
        callback = () => { EditorApplication.update -= callback; completion.SetResult(true); };
        EditorApplication.update += callback;
        return completion.Task;
    }

    public static async void Run()
    {
        var report = new JObject { ["boundary"] = "accepted in-process queue; TCP peer admission bypassed" };
        string root = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
        string output = Path.Combine(root, "Library", "AsyncWriteExecutionFixture");
        Directory.CreateDirectory(output);
        int exit = 1;
        try
        {
            VRCForgeMcpCoreServer.Start();
            Require(!EditorApplication.isPlaying, "fixture requires edit mode");
            string folder = "Assets/AsyncWriteFixture_" + Guid.NewGuid().ToString("N");
            Directory.CreateDirectory(Path.Combine(root, folder));
            AssetDatabase.Refresh();
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var target = new GameObject("AsyncWriteTarget");
            string scenePath = folder + "/Fixture.unity";
            Require(EditorSceneManager.SaveScene(scene, scenePath), "fixture scene save failed");
            File.Copy(Path.Combine(root, scenePath), Path.Combine(output, "before.unity"), true);
            File.Copy(Path.Combine(root, scenePath + ".meta"), Path.Combine(output, "before.unity.meta"), true);
            report["scenePath"] = scenePath;
            report["rollbackBaseline"] = Path.Combine(output, "before.unity");

            const string tool = "vrc_set_gameobject_active";
            var arguments = new JObject { ["gameObjectPath"] = "AsyncWriteTarget", ["active"] = false };
            string executionId = "fixture-" + Guid.NewGuid().ToString("N");
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            var context = new JObject {
                ["executionId"] = executionId, ["approvalId"] = "isolated-fixture",
                ["checkpointId"] = Path.Combine(output, "before.unity"),
                ["targetTool"] = tool, ["unityToolName"] = tool,
                ["argumentsSha256"] = (string)Call(Server, "ComputeCanonicalJsonHash", arguments),
                ["projectId"] = (string)Server.GetField("descriptorProjectId", Static).GetValue(null),
                ["instanceId"] = (string)Server.GetField("descriptorInstanceId", Static).GetValue(null),
                ["issuedAtUnixMs"] = now - 1000, ["expiresAtUnixMs"] = now + 60000
            };
            var pending = Activator.CreateInstance(Server.GetNestedType("PendingInvocation", BindingFlags.NonPublic), true);
            Set(pending, "ToolName", tool);
            Set(pending, "Arguments", arguments);
            Set(pending, "ExecutionContext", context);
            Set(pending, "Lane", Enum.Parse(Server.GetNestedType("InvocationLane", BindingFlags.NonPublic), "ApprovedWrite"));
            Set(pending, "AsyncWrite", true);
            Set(pending, "Modern", true);
            Set(pending, "ExecutionId", executionId);
            int beforeGameFrame = Time.frameCount;
            Call(Server, "QueueMcpWriteExecution", pending);
            Require(target.activeSelf, "accepted write executed before the next editor update");
            await NextEditorUpdate();
            var pollArguments = new JObject { ["executionId"] = executionId };
            var completed = (JObject)Call(Server, "ReadMcpWriteOperation", pollArguments);
            report["firstReceipt"] = completed.DeepClone();
            Require((string)completed["schema"] == "vrcforge.write-operation.v1", "polling response schema missing");
            Require((string)completed["executionId"] == executionId, "polling response identity mismatch");
            Require((string)completed["status"] == "done", "first queue did not complete");
            Require(completed["result"]?["isError"]?.Value<bool>() == false, "first handler returned an error");
            Require(!target.activeSelf, "real set-active handler did not run");
            Require(!scene.isDirty, "real handler did not save the scene");
            report["gameFrameBefore"] = beforeGameFrame;
            report["gameFrameAfter"] = Time.frameCount;
            report["editorUpdateExecution"] = true;

            // A second dispatch of the same accepted identity must not run the handler.
            target.SetActive(true);
            Call(Server, "QueueMcpWriteExecution", pending);
            await NextEditorUpdate();
            var replay = (JObject)Call(Server, "ReadMcpWriteOperation", pollArguments);
            report["replayReceipt"] = replay.DeepClone();
            Require(target.activeSelf, "duplicate dispatch executed the handler again");
            Require(JToken.DeepEquals(replay, completed), "duplicate dispatch overwrote the terminal receipt");
            report["passed"] = true;
            exit = 0;
        }
        catch (Exception error)
        {
            report["passed"] = false;
            report["error"] = error.ToString();
            Debug.LogException(error);
        }
        finally
        {
            File.WriteAllText(Path.Combine(output, "result.json"), report.ToString());
            Debug.Log("ASYNC_WRITE_FIXTURE " + report);
            EditorApplication.Exit(exit);
        }
    }
}

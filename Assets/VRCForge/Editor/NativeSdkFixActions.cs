#if !VRC_CLIENT
using System;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace VRCForge.Editor
{
    // Narrow adapter for the VRChat SDK's cached, opaque Issue.fixThisIssue callback.
    // The shared dispatcher supplies the existing approval/checkpoint boundary.
    internal static class NativeSdkFixActions
    {
        internal static Action Resolve(JObject selector)
        {
            if (selector == null) throw new ArgumentNullException(nameof(selector));
            var binding = VrchatSdkBuilderAlertsTool.ResolveNativeFixBinding(selector);
            return () => Invoke(binding);
        }

        private static void Invoke(VrchatSdkBuilderAlertsTool.NativeFixBinding binding)
        {
            binding.FixAction();

            // Match the SDK's own Auto Fix button post-action behavior.
            EditorSceneManager.MarkSceneDirty(SceneManager.GetActiveScene());
            EditorUtility.SetDirty(binding.Subject);
            var refresh = VrchatSdkBuilderAlertsTool.FindInstanceMethod(binding.Panel.GetType(), "RunValidations");
            if (refresh == null || refresh.GetParameters().Length != 0)
                throw new InvalidOperationException("The SDK panel cannot refresh Builder validations after the native action.");
            try
            {
                refresh.Invoke(binding.Panel, null);
            }
            catch (TargetInvocationException exception) when (exception.InnerException != null)
            {
                throw exception.InnerException;
            }
        }
    }
}
#endif

#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Core.MCP
{
    /// <summary>
    /// Process-local bridge between approval-known continuation nonces and a
    /// Unity-assigned GlobalObjectId that does not exist until instantiation.
    /// Entries are ordered, short-lived, and fail closed across domain reload.
    /// </summary>
    public static class VRCForgeApprovedObjectReceipt
    {
        private static readonly object Gate = new object();
        private static readonly Dictionary<string, Receipt> Receipts = new Dictionary<string, Receipt>(StringComparer.Ordinal);
        private static readonly TimeSpan Lifetime = TimeSpan.FromMinutes(10);
        private static readonly HashSet<string> AllowedContinuationTools = new HashSet<string>(StringComparer.Ordinal)
        {
            "vrc_unpack_prefab",
            "vrc_setup_outfit",
            "vrc_add_wardrobe_outfit",
        };

        private sealed class Receipt
        {
            public string GlobalObjectId = "";
            public string ScenePath = "";
            public string HierarchyPath = "";
            public string[] Tools = Array.Empty<string>();
            public int NextIndex;
            public DateTime ExpiresUtc;
            public bool Bound;
        }

        public static int Reserve(string nonce, IEnumerable<string> continuationTools)
        {
            ValidateNonce(nonce);
            var tools = ValidateTools(continuationTools);
            var receipt = new Receipt
            {
                Tools = tools,
                NextIndex = 0,
                ExpiresUtc = DateTime.UtcNow + Lifetime,
                Bound = false,
            };
            lock (Gate)
            {
                PruneExpired();
                if (Receipts.ContainsKey(nonce))
                {
                    throw new InvalidOperationException("Approval-bound continuation nonce was already registered.");
                }
                Receipts.Add(nonce, receipt);
            }
            return tools.Length;
        }

        public static string Bind(string nonce, GameObject target)
        {
            ValidateNonce(nonce);
            if (target == null || !target.scene.IsValid() || EditorUtility.IsPersistent(target))
            {
                throw new InvalidOperationException("Approval-bound continuation target is invalid.");
            }
            var globalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(target).ToString();
            if (string.IsNullOrWhiteSpace(globalObjectId))
            {
                throw new InvalidOperationException("Approval-bound continuation target has no stable GlobalObjectId.");
            }
            lock (Gate)
            {
                PruneExpired();
                if (!Receipts.TryGetValue(nonce, out var receipt) || receipt.Bound)
                {
                    Receipts.Remove(nonce);
                    throw new InvalidOperationException("Approval-bound continuation reservation is unavailable.");
                }
                receipt.GlobalObjectId = globalObjectId;
                receipt.ScenePath = target.scene.path ?? "";
                receipt.HierarchyPath = HierarchyPath(target.transform);
                receipt.Bound = true;
                return globalObjectId;
            }
        }

        public static void CancelReservation(string nonce)
        {
            if (string.IsNullOrWhiteSpace(nonce))
            {
                return;
            }
            lock (Gate)
            {
                if (Receipts.TryGetValue(nonce, out var receipt) && !receipt.Bound)
                {
                    Receipts.Remove(nonce);
                }
            }
        }

        public static string Consume(string nonce, string toolName, GameObject target)
        {
            ValidateNonce(nonce);
            if (!AllowedContinuationTools.Contains(toolName) || target == null)
            {
                throw new InvalidOperationException("Approval-bound continuation request is invalid.");
            }
            lock (Gate)
            {
                PruneExpired();
                if (!Receipts.TryGetValue(nonce, out var receipt) || !receipt.Bound)
                {
                    throw new InvalidOperationException("Approval-bound object continuation is unavailable or expired.");
                }
                if (receipt.NextIndex >= receipt.Tools.Length || !string.Equals(receipt.Tools[receipt.NextIndex], toolName, StringComparison.Ordinal))
                {
                    Receipts.Remove(nonce);
                    throw new InvalidOperationException("Approval-bound object continuation order drifted.");
                }
                var globalObjectId = GlobalObjectId.GetGlobalObjectIdSlow(target).ToString();
                if (!string.Equals(globalObjectId, receipt.GlobalObjectId, StringComparison.Ordinal)
                    || !string.Equals(target.scene.path ?? "", receipt.ScenePath, StringComparison.Ordinal)
                    || !string.Equals(HierarchyPath(target.transform), receipt.HierarchyPath, StringComparison.Ordinal))
                {
                    Receipts.Remove(nonce);
                    throw new InvalidOperationException("Approval-bound object continuation target was replaced or moved.");
                }
                receipt.NextIndex += 1;
                if (receipt.NextIndex == receipt.Tools.Length)
                {
                    Receipts.Remove(nonce);
                }
                return globalObjectId;
            }
        }

        private static void PruneExpired()
        {
            var now = DateTime.UtcNow;
            foreach (var nonce in Receipts.Where(pair => pair.Value.ExpiresUtc <= now).Select(pair => pair.Key).ToArray())
            {
                Receipts.Remove(nonce);
            }
        }

        private static void ValidateNonce(string nonce)
        {
            if (string.IsNullOrWhiteSpace(nonce) || nonce.Length != 64 || nonce.Any(character => !Uri.IsHexDigit(character)))
            {
                throw new InvalidOperationException("Approval-bound continuation nonce is invalid.");
            }
        }

        private static string[] ValidateTools(IEnumerable<string> continuationTools)
        {
            var tools = (continuationTools ?? Array.Empty<string>()).ToArray();
            if (tools.Length == 0
                || tools.Length > 3
                || tools.Distinct(StringComparer.Ordinal).Count() != tools.Length
                || tools.Any(tool => !AllowedContinuationTools.Contains(tool)))
            {
                throw new InvalidOperationException("Approval-bound continuation tool sequence is invalid.");
            }
            return tools;
        }

        private static string HierarchyPath(Transform transform)
        {
            var segments = new List<string>();
            for (var current = transform; current != null; current = current.parent)
            {
                segments.Insert(0, current.name);
            }
            return string.Join("/", segments);
        }
    }
}
#endif

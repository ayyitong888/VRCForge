using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace VRCForge.Editor
{
    internal sealed class TransformPreservationSnapshot
    {
        internal sealed class Report
        {
            public string ownerPath { get; private set; }
            public int snapshotCount { get; private set; }
            public int driftedCount { get; private set; }
            public int restoredCount { get; private set; }
            public int skippedCount { get; private set; }
            public bool preserved
            {
                get { return snapshotCount > 0 && skippedCount == 0 && driftedCount == restoredCount; }
            }

            internal Report(string ownerPath, int snapshotCount)
            {
                this.ownerPath = ownerPath ?? string.Empty;
                this.snapshotCount = snapshotCount;
            }

            internal void MarkDrifted()
            {
                driftedCount++;
            }

            internal void MarkRestored()
            {
                restoredCount++;
            }

            internal void MarkSkipped()
            {
                skippedCount++;
            }
        }

        private sealed class Entry
        {
            internal Transform Transform;
            internal string Path;
            internal Vector3 LocalPosition;
            internal Quaternion LocalRotation;
            internal Vector3 LocalScale;
        }

        private readonly Transform owner;
        private readonly Entry[] entries;

        private TransformPreservationSnapshot(Transform owner, IEnumerable<Entry> entries)
        {
            this.owner = owner;
            this.entries = (entries ?? Enumerable.Empty<Entry>()).ToArray();
        }

        internal static TransformPreservationSnapshot Capture(Transform owner, Transform excludedRoot = null)
        {
            var entries = owner == null
                ? Enumerable.Empty<Entry>()
                : owner.GetComponentsInChildren<Transform>(true)
                    .Where(transform => transform != null && !IsInSubtree(transform, excludedRoot))
                    .Select(transform => new Entry
                    {
                        Transform = transform,
                        Path = GetPath(transform),
                        LocalPosition = transform.localPosition,
                        LocalRotation = transform.localRotation,
                        LocalScale = transform.localScale,
                    });
            return new TransformPreservationSnapshot(owner, entries);
        }

        internal static Transform FindHumanoidOwner(Transform start)
        {
            if (start == null)
            {
                return null;
            }

            for (var current = start; current != null; current = current.parent)
            {
                var animator = current.GetComponent<Animator>();
                if (animator != null && animator.isHuman)
                {
                    return animator.transform;
                }
            }

            return null;
        }

        internal Report Restore(string undoName)
        {
            var report = new Report(GetPath(owner), entries.Length);
            foreach (var entry in entries)
            {
                var transform = entry.Transform;
                if (transform == null)
                {
                    throw new InvalidOperationException(
                        $"Avatar transform '{entry.Path}' was destroyed during the guarded operation.");
                }

                if (Matches(transform, entry))
                {
                    continue;
                }

                report.MarkDrifted();
                Undo.RecordObject(transform, undoName);
                transform.localPosition = entry.LocalPosition;
                transform.localRotation = entry.LocalRotation;
                transform.localScale = entry.LocalScale;
                PrefabUtility.RecordPrefabInstancePropertyModifications(transform);
                EditorUtility.SetDirty(transform);
                if (!Matches(transform, entry))
                {
                    throw new InvalidOperationException(
                        $"Avatar transform '{entry.Path}' could not be restored exactly.");
                }
                report.MarkRestored();
            }

            return report;
        }

        private static bool Matches(Transform transform, Entry entry)
        {
            return transform.localPosition == entry.LocalPosition
                && transform.localRotation == entry.LocalRotation
                && transform.localScale == entry.LocalScale;
        }

        internal static string GetPath(Transform transform)
        {
            if (transform == null)
            {
                return string.Empty;
            }

            var segments = new List<string>();
            for (var current = transform; current != null; current = current.parent)
            {
                segments.Insert(0, current.name);
            }

            return string.Join("/", segments);
        }

        private static bool IsInSubtree(Transform transform, Transform subtreeRoot)
        {
            if (transform == null || subtreeRoot == null)
            {
                return false;
            }

            for (var current = transform; current != null; current = current.parent)
            {
                if (current == subtreeRoot)
                {
                    return true;
                }
            }

            return false;
        }
    }
}

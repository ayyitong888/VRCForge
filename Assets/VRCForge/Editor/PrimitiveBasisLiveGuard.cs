using System;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace VRCForge.Editor
{
    internal static class PrimitiveBasisLiveGuard
    {
        internal const string RunIdEnvironment = "VRCFORGE_PRIMITIVE_BASIS_RUN_ID";

        internal sealed class ProcessIdentity
        {
            internal int ProcessId;
            internal string StartedAtUtc;
            internal string ExecutableDigest;
            internal string ProjectPathDigest;
            internal string RunIdDigest;
        }

        internal static ProcessIdentity InspectBootstrap(string expectedRunIdDigest)
        {
            var liveRunId = Environment.GetEnvironmentVariable(RunIdEnvironment) ?? string.Empty;
            if (string.IsNullOrWhiteSpace(liveRunId)
                || !IsSha256(expectedRunIdDigest)
                || !string.Equals(Sha256Text(liveRunId), expectedRunIdDigest, StringComparison.Ordinal))
            {
                throw new InvalidOperationException("The fixed live-run identity is invalid.");
            }

            using (var process = Process.GetCurrentProcess())
            {
                var executablePath = process.MainModule?.FileName;
                if (string.IsNullOrWhiteSpace(executablePath) || !File.Exists(executablePath))
                {
                    throw new InvalidOperationException("The Unity process executable is unavailable.");
                }
                var projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
                return new ProcessIdentity
                {
                    ProcessId = process.Id,
                    StartedAtUtc = process.StartTime.ToUniversalTime().ToString("O"),
                    ExecutableDigest = Sha256File(executablePath),
                    ProjectPathDigest = Sha256Text(NormalizeProjectRoot(projectRoot)),
                    RunIdDigest = expectedRunIdDigest
                };
            }
        }

        internal static ProcessIdentity RequireBoundRequest(JObject parameters)
        {
            parameters = parameters ?? new JObject();
            var liveRunId = Environment.GetEnvironmentVariable(RunIdEnvironment) ?? string.Empty;
            var supplied = parameters["expectedRunIdDigest"] != null
                || parameters["expectedProjectPathDigest"] != null
                || parameters["expectedUnityProcessId"] != null
                || parameters["expectedUnityProcessStartedAtUtc"] != null
                || parameters["expectedUnityExecutableDigest"] != null;
            if (string.IsNullOrWhiteSpace(liveRunId) && !supplied)
            {
                return null;
            }

            var expectedRunIdDigest = (parameters["expectedRunIdDigest"]?.ToString() ?? string.Empty).Trim();
            var expectedProjectPathDigest = (parameters["expectedProjectPathDigest"]?.ToString() ?? string.Empty).Trim();
            var expectedStartedAtUtc = (parameters["expectedUnityProcessStartedAtUtc"]?.ToString() ?? string.Empty).Trim();
            var expectedExecutableDigest = (parameters["expectedUnityExecutableDigest"]?.ToString() ?? string.Empty).Trim();
            var processToken = parameters["expectedUnityProcessId"];
            if (!IsSha256(expectedRunIdDigest)
                || !IsSha256(expectedProjectPathDigest)
                || !IsSha256(expectedExecutableDigest)
                || string.IsNullOrWhiteSpace(expectedStartedAtUtc)
                || processToken == null
                || processToken.Type != JTokenType.Integer)
            {
                throw new InvalidOperationException("The fixed live transport binding is incomplete.");
            }

            var actual = InspectBootstrap(expectedRunIdDigest);
            var expectedProcessId = processToken.ToObject<int>();
            if (actual.ProcessId != expectedProcessId
                || !string.Equals(actual.ProjectPathDigest, expectedProjectPathDigest, StringComparison.Ordinal)
                || !string.Equals(actual.StartedAtUtc, expectedStartedAtUtc, StringComparison.Ordinal)
                || !string.Equals(actual.ExecutableDigest, expectedExecutableDigest, StringComparison.Ordinal))
            {
                throw new InvalidOperationException("The fixed live transport binding changed.");
            }
            return actual;
        }

        internal static string Sha256File(string path)
        {
            using (var sha256 = SHA256.Create())
            using (var stream = new FileStream(
                path,
                FileMode.Open,
                FileAccess.Read,
                FileShare.ReadWrite | FileShare.Delete))
            {
                var bytes = sha256.ComputeHash(stream);
                return Hex(bytes);
            }
        }

        internal static string Sha256Text(string value)
        {
            using (var sha256 = SHA256.Create())
            {
                return Hex(sha256.ComputeHash(Encoding.UTF8.GetBytes(value ?? string.Empty)));
            }
        }

        internal static string NormalizeProjectRoot(string path)
        {
            return Path.GetFullPath(path)
                .Replace("\\", "/")
                .TrimEnd('/')
                .ToLowerInvariant();
        }

        internal static bool IsSha256(string value)
        {
            if (value == null || value.Length != 64) { return false; }
            foreach (var character in value)
            {
                if ((character < '0' || character > '9') && (character < 'a' || character > 'f'))
                {
                    return false;
                }
            }
            return true;
        }

        private static string Hex(byte[] bytes)
        {
            var builder = new StringBuilder(bytes.Length * 2);
            foreach (var item in bytes) { builder.Append(item.ToString("x2")); }
            return builder.ToString();
        }
    }
}

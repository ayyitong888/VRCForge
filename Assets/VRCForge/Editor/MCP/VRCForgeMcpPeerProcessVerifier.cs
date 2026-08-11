using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace VRCForge.Editor
{
    // This verifier is intentionally a narrow, Windows-only eligibility screen for
    // the accepted loopback Core connection. It reads the OS TCP owner table
    // instead of any caller-supplied PID. The Core owns its call lifetime: call
    // it immediately before App preview/safety/approved-write operations and
    // discard the evidence when the TcpClient is closed. The bearer proves local
    // capability possession; the release-paired hashes screen the expected managed
    // payload. This does not claim publisher identity or defend against a caller
    // that can modify the imported Core, inspect its memory, or inject processes.
    internal sealed class VRCForgeMcpPeerProcessEvidence
    {
        internal int ProcessId { get; private set; }
        internal string ProcessPath { get; private set; }
        internal long ProcessStartTimeUtcTicks { get; private set; }
        internal int ParentProcessId { get; private set; }
        internal string ParentProcessPath { get; private set; }
        internal long ParentProcessStartTimeUtcTicks { get; private set; }

        internal VRCForgeMcpPeerProcessEvidence(
            int processId,
            string processPath,
            long processStartTimeUtcTicks,
            int parentProcessId,
            string parentProcessPath,
            long parentProcessStartTimeUtcTicks)
        {
            ProcessId = processId;
            ProcessPath = processPath;
            ProcessStartTimeUtcTicks = processStartTimeUtcTicks;
            ParentProcessId = parentProcessId;
            ParentProcessPath = parentProcessPath;
            ParentProcessStartTimeUtcTicks = parentProcessStartTimeUtcTicks;
        }

        internal bool Matches(VRCForgeMcpPeerProcessEvidence other)
        {
            return other != null
                && ProcessId == other.ProcessId
                && ProcessStartTimeUtcTicks == other.ProcessStartTimeUtcTicks
                && ParentProcessId == other.ParentProcessId
                && ParentProcessStartTimeUtcTicks == other.ParentProcessStartTimeUtcTicks
                && string.Equals(ProcessPath, other.ProcessPath, StringComparison.OrdinalIgnoreCase)
                && string.Equals(ParentProcessPath, other.ParentProcessPath, StringComparison.OrdinalIgnoreCase);
        }
    }

    internal static class VRCForgeMcpPeerProcessVerifier
    {
        private const int AfInet = 2;
        private const int TcpTableOwnerPidAll = 5;
        private const int ErrorInsufficientBuffer = 122;

        [StructLayout(LayoutKind.Sequential)]
        private struct MibTcpRowOwnerPid
        {
            internal uint State;
            internal uint LocalAddress;
            internal uint LocalPort;
            internal uint RemoteAddress;
            internal uint RemotePort;
            internal uint OwningPid;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct ProcessEntry32
        {
            internal uint Size;
            internal uint Usage;
            internal uint ProcessId;
            internal IntPtr DefaultHeapId;
            internal uint ModuleId;
            internal uint Threads;
            internal uint ParentProcessId;
            internal int PriorityClassBase;
            internal uint Flags;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)]
            internal string ExecutableFile;
        }

        [DllImport("iphlpapi.dll", SetLastError = true)]
        private static extern uint GetExtendedTcpTable(
            IntPtr tcpTable,
            ref int outBufferLength,
            bool sort,
            int ipVersion,
            int tableClass,
            uint reserved);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint processId);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool Process32First(IntPtr snapshot, ref ProcessEntry32 entry);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool Process32Next(IntPtr snapshot, ref ProcessEntry32 entry);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CloseHandle(IntPtr handle);

        internal static bool TryScreenManagedBackendPeer(TcpClient client, out VRCForgeMcpPeerProcessEvidence evidence)
        {
            evidence = null;
            if (Environment.OSVersion.Platform != PlatformID.Win32NT || client == null || client.Client == null)
            {
                return false;
            }

            try
            {
                var local = client.Client.LocalEndPoint as IPEndPoint;
                var remote = client.Client.RemoteEndPoint as IPEndPoint;
                if (!IsStrictLoopback(local) || !IsStrictLoopback(remote))
                {
                    return false;
                }

                var processId = FindTcpOwnerProcessId(local, remote);
                if (processId <= 0)
                {
                    return false;
                }

                long processStartTimeUtcTicks;
                var processPath = ReadExpectedProcessPath(processId, "vrcforge_backend", out processStartTimeUtcTicks);
                var parentProcessId = ReadParentProcessId(processId);
                if (parentProcessId <= 0)
                {
                    return false;
                }
                long parentProcessStartTimeUtcTicks;
                var parentProcessPath = ReadExpectedProcessPath(parentProcessId, "VRCForge", out parentProcessStartTimeUtcTicks);
                if (string.IsNullOrEmpty(processPath) || processStartTimeUtcTicks <= 0
                    || string.IsNullOrEmpty(parentProcessPath) || parentProcessStartTimeUtcTicks <= 0)
                {
                    return false;
                }
                if (!VerifyPairedReleasePayload(processPath, parentProcessPath))
                {
                    return false;
                }

                evidence = new VRCForgeMcpPeerProcessEvidence(
                    processId,
                    processPath,
                    processStartTimeUtcTicks,
                    parentProcessId,
                    parentProcessPath,
                    parentProcessStartTimeUtcTicks);
                return true;
            }
            catch (Exception)
            {
                evidence = null;
                return false;
            }
        }

        private static bool IsStrictLoopback(IPEndPoint endpoint)
        {
            return endpoint != null
                && endpoint.AddressFamily == AddressFamily.InterNetwork
                && endpoint.Address != null
                && endpoint.Address.Equals(IPAddress.Loopback)
                && endpoint.Port > 0;
        }

        private static int FindTcpOwnerProcessId(IPEndPoint local, IPEndPoint remote)
        {
            var bytesNeeded = 0;
            var status = GetExtendedTcpTable(IntPtr.Zero, ref bytesNeeded, false, AfInet, TcpTableOwnerPidAll, 0);
            if (status != ErrorInsufficientBuffer || bytesNeeded <= sizeof(uint))
            {
                return 0;
            }

            var memory = IntPtr.Zero;
            try
            {
                memory = Marshal.AllocHGlobal(bytesNeeded);
                status = GetExtendedTcpTable(memory, ref bytesNeeded, false, AfInet, TcpTableOwnerPidAll, 0);
                if (status != 0)
                {
                    return 0;
                }
                var count = Marshal.ReadInt32(memory);
                if (count < 0 || count > 65535)
                {
                    return 0;
                }
                var rowSize = Marshal.SizeOf(typeof(MibTcpRowOwnerPid));
                var expectedLocalAddress = ToTcpAddress(remote.Address);
                var expectedLocalPort = ToTcpPort(remote.Port);
                var expectedRemoteAddress = ToTcpAddress(local.Address);
                var expectedRemotePort = ToTcpPort(local.Port);
                for (var index = 0; index < count; index++)
                {
                    var rowAddress = IntPtr.Add(memory, sizeof(uint) + (index * rowSize));
                    var row = (MibTcpRowOwnerPid)Marshal.PtrToStructure(rowAddress, typeof(MibTcpRowOwnerPid));
                    if (row.LocalAddress == expectedLocalAddress
                        && (row.LocalPort & 0xffffU) == expectedLocalPort
                        && row.RemoteAddress == expectedRemoteAddress
                        && (row.RemotePort & 0xffffU) == expectedRemotePort
                        && row.OwningPid > 0 && row.OwningPid <= int.MaxValue)
                    {
                        return (int)row.OwningPid;
                    }
                }
                return 0;
            }
            finally
            {
                if (memory != IntPtr.Zero)
                {
                    Marshal.FreeHGlobal(memory);
                }
            }
        }

        private static uint ToTcpAddress(IPAddress address)
        {
            var bytes = address.GetAddressBytes();
            if (bytes == null || bytes.Length != 4)
            {
                throw new InvalidOperationException();
            }
            return BitConverter.ToUInt32(bytes, 0);
        }

        private static uint ToTcpPort(int port)
        {
            if (port <= 0 || port > 65535)
            {
                throw new ArgumentOutOfRangeException("port");
            }
            return (uint)(((port & 0xff) << 8) | ((port >> 8) & 0xff));
        }

        private static string ReadExpectedProcessPath(int processId, string expectedName, out long startTimeUtcTicks)
        {
            startTimeUtcTicks = 0;
            try
            {
                using (var process = Process.GetProcessById(processId))
                {
                    var name = process.ProcessName;
                    var path = process.MainModule == null ? null : process.MainModule.FileName;
                    if (string.IsNullOrEmpty(name) || string.IsNullOrEmpty(path)
                        || !string.Equals(name, expectedName, StringComparison.OrdinalIgnoreCase)
                        || !string.Equals(Path.GetFileNameWithoutExtension(path), expectedName, StringComparison.OrdinalIgnoreCase))
                    {
                        return null;
                    }
                    startTimeUtcTicks = process.StartTime.ToUniversalTime().Ticks;
                    return path;
                }
            }
            catch (Exception)
            {
                return null;
            }
        }

        private static bool VerifyPairedReleasePayload(string backendPath, string parentPath)
        {
            // The accepted process lifetime is the concrete packaged desktop
            // process and its direct packaged backend child. Do not accept a
            // look-alike executable tree or caller-supplied location.
            var root = Path.GetDirectoryName(Path.GetFullPath(parentPath));
            if (string.IsNullOrEmpty(root))
            {
                return false;
            }
            var expectedParent = Path.GetFullPath(Path.Combine(root, "VRCForge.exe"));
            var expectedBackend = Path.GetFullPath(Path.Combine(root, "backend", "vrcforge_backend.exe"));
            if (!string.Equals(Path.GetFullPath(parentPath), expectedParent, StringComparison.OrdinalIgnoreCase)
                || !string.Equals(Path.GetFullPath(backendPath), expectedBackend, StringComparison.OrdinalIgnoreCase)
                || !IsDirectoryWithoutReparse(root)
                || !IsDirectoryWithoutReparse(Path.GetDirectoryName(expectedBackend))
                || !IsRegularFileWithoutReparse(expectedParent)
                || !IsRegularFileWithoutReparse(expectedBackend))
            {
                return false;
            }

            string expectedDesktopDigest;
            string expectedBackendDigest;
            if (!TryReadTrustedReleaseDigests(out expectedDesktopDigest, out expectedBackendDigest))
            {
                return false;
            }

            var manifestPath = Path.Combine(root, "payload-integrity.json");
            if (!IsRegularFileWithoutReparse(manifestPath))
            {
                return false;
            }
            var manifest = JObject.Parse(File.ReadAllText(manifestPath));
            if (!string.Equals((string)manifest["schema"], "vrcforge.payload-integrity.v1", StringComparison.Ordinal))
            {
                return false;
            }
            var files = manifest["files"] as JObject;
            return VerifyIntegrityEntry(files, "desktop", "VRCForge.exe", expectedParent, expectedDesktopDigest)
                && VerifyIntegrityEntry(files, "backend", "backend/vrcforge_backend.exe", expectedBackend, expectedBackendDigest);
        }

        private static bool TryReadTrustedReleaseDigests(
            out string expectedDesktopDigest,
            out string expectedBackendDigest)
        {
            expectedDesktopDigest = null;
            expectedBackendDigest = null;
            try
            {
                var projectRoot = Path.GetDirectoryName(Path.GetFullPath(Application.dataPath));
                if (string.IsNullOrEmpty(projectRoot))
                {
                    return false;
                }
                var manifestPath = Path.GetFullPath(Path.Combine(
                    projectRoot,
                    VRCForgeMcpTrustedRelease.AssetPath.Replace('/', Path.DirectorySeparatorChar)));
                if (!IsRegularFileWithoutReparse(manifestPath))
                {
                    return false;
                }
                var manifest = JObject.Parse(File.ReadAllText(manifestPath));
                if (manifest.Count != 3
                    || !string.Equals((string)manifest["schema"], "vrcforge.trusted-release.v1", StringComparison.Ordinal))
                {
                    return false;
                }
                expectedDesktopDigest = (string)manifest["desktopSha256"];
                expectedBackendDigest = (string)manifest["backendSha256"];
                return IsLowerSha256(expectedDesktopDigest) && IsLowerSha256(expectedBackendDigest);
            }
            catch (Exception)
            {
                expectedDesktopDigest = null;
                expectedBackendDigest = null;
                return false;
            }
        }

        private static bool VerifyIntegrityEntry(
            JObject files,
            string key,
            string relativePath,
            string actualPath,
            string releaseDigest)
        {
            var entry = files == null ? null : files[key] as JObject;
            var manifestDigest = entry == null ? null : (string)entry["sha256"];
            if (entry == null
                || !string.Equals((string)entry["relativePath"], relativePath, StringComparison.Ordinal)
                || !IsLowerSha256(manifestDigest)
                || !ConstantTimeTextEquals(manifestDigest, releaseDigest))
            {
                return false;
            }
            return ConstantTimeTextEquals(releaseDigest, ComputeSha256(actualPath));
        }

        private static bool IsLowerSha256(string digest)
        {
            if (string.IsNullOrEmpty(digest) || digest.Length != 64)
            {
                return false;
            }
            for (var index = 0; index < digest.Length; index++)
            {
                var value = digest[index];
                if (!((value >= '0' && value <= '9') || (value >= 'a' && value <= 'f')))
                {
                    return false;
                }
            }
            return true;
        }

        private static string ComputeSha256(string path)
        {
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (var sha256 = SHA256.Create())
            {
                var digest = sha256.ComputeHash(stream);
                var builder = new System.Text.StringBuilder(digest.Length * 2);
                foreach (var value in digest)
                {
                    builder.Append(value.ToString("x2"));
                }
                return builder.ToString();
            }
        }

        private static bool IsDirectoryWithoutReparse(string path)
        {
            if (string.IsNullOrEmpty(path))
            {
                return false;
            }
            var directory = new DirectoryInfo(path);
            return directory.Exists
                && (directory.Attributes & FileAttributes.Directory) != 0
                && (directory.Attributes & FileAttributes.ReparsePoint) == 0;
        }

        private static bool IsRegularFileWithoutReparse(string path)
        {
            if (string.IsNullOrEmpty(path))
            {
                return false;
            }
            var file = new FileInfo(path);
            return file.Exists
                && (file.Attributes & FileAttributes.Directory) == 0
                && (file.Attributes & FileAttributes.ReparsePoint) == 0;
        }

        private static bool ConstantTimeTextEquals(string left, string right)
        {
            if (left == null || right == null)
            {
                return false;
            }
            var difference = left.Length ^ right.Length;
            var length = Math.Max(left.Length, right.Length);
            for (var index = 0; index < length; index++)
            {
                var leftValue = index < left.Length ? left[index] : '\0';
                var rightValue = index < right.Length ? right[index] : '\0';
                difference |= leftValue ^ rightValue;
            }
            return difference == 0;
        }

        private static int ReadParentProcessId(int processId)
        {
            var snapshot = CreateToolhelp32Snapshot(0x00000002, 0);
            if (snapshot == IntPtr.Zero || snapshot == new IntPtr(-1))
            {
                return 0;
            }
            try
            {
                var entry = new ProcessEntry32 { Size = (uint)Marshal.SizeOf(typeof(ProcessEntry32)) };
                if (!Process32First(snapshot, ref entry))
                {
                    return 0;
                }
                do
                {
                    if (entry.ProcessId == (uint)processId && entry.ParentProcessId > 0 && entry.ParentProcessId <= int.MaxValue)
                    {
                        return (int)entry.ParentProcessId;
                    }
                    entry.Size = (uint)Marshal.SizeOf(typeof(ProcessEntry32));
                }
                while (Process32Next(snapshot, ref entry));
                return 0;
            }
            finally
            {
                CloseHandle(snapshot);
            }
        }
    }
}

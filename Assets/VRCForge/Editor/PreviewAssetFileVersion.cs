using System;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
using UnityEngine;

namespace VRCForge.Editor
{
    internal static class PreviewAssetFileVersion
    {
        internal static string Read(FileStream stream)
        {
            if (Application.platform != RuntimePlatform.WindowsEditor)
                return string.Empty;

            if (!GetFileInformationByHandleEx(stream.SafeFileHandle, 0, out var basic,
                    (uint)Marshal.SizeOf<BasicInfo>()))
            {
                throw new IOException("The project file version could not be read.");
            }
            return FormattableString.Invariant($"{basic.CreationTime}:{basic.LastWriteTime}:{basic.ChangeTime}:{stream.Length}");
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct BasicInfo
        {
            internal long CreationTime;
            internal long LastAccessTime;
            internal long LastWriteTime;
            internal long ChangeTime;
            internal uint FileAttributes;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool GetFileInformationByHandleEx(
            SafeFileHandle file, int infoClass, out BasicInfo info, uint size);
    }
}

using System;
using UnityEngine;
namespace VRCForge
{
    // Owned by one Editor observation job; never attached to user objects or saved.
    [DefaultExecutionOrder(32000)]
    public sealed class RuntimeObservationFramePump : MonoBehaviour
    {
        public Action Tick;
        public Action Lost;
        private void LateUpdate() { Tick?.Invoke(); }
        private void OnDestroy() { var lost = Lost; Tick = null; Lost = null; lost?.Invoke(); }
    }
}

using System;
using System.Collections.Generic;
using UnityEngine;

namespace VRCForge.LocalReview
{
    [Serializable]
    public sealed class SerializedPluginFixtureSettings
    {
        public int count = 3;
    }

    [Serializable]
    public sealed class SerializedPluginFixtureEntry
    {
        public float weight = 1.25f;
    }

    // This component intentionally contains only plain serialized data fields.
    // UnityComponentCrud's checkpoint contract rejects executable plugin behavior.
    public sealed class SerializedPluginFixtureData : MonoBehaviour
    {
        public SerializedPluginFixtureSettings settings = new SerializedPluginFixtureSettings();
        public List<SerializedPluginFixtureEntry> entries = new List<SerializedPluginFixtureEntry>
        {
            new SerializedPluginFixtureEntry { weight = 0.5f },
            new SerializedPluginFixtureEntry { weight = 1.25f },
        };
        public GameObject linkedObject;
        public int legacyValue = 7;
    }
}

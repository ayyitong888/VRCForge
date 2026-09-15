using UnityEngine;

namespace VRCForge.Editor
{
    internal sealed class WardrobeSceneSaveScope
    {
        private readonly SavedSceneSnapshot scene;

        internal WardrobeSceneSaveScope(GameObject target)
        {
            if (target != null)
                scene = ComponentCrudCore.ResolveSavedSceneFor(target);
        }

        internal void Save()
        {
            if (scene != null)
                ComponentCrudCore.SaveAndResolveScene(scene);
        }
    }
}

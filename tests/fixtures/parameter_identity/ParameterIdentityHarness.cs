using System;
using System.Collections.Generic;
using System.Linq;

public static class ParameterIdentityHarness
{
    /* PRODUCT_METHOD */

    public static int Main()
    {
        if (ValidateRequestedParameterNames(new[] { "Mood" }, new[] { "Mood", "mood" }).Count != 1) return 1;
        try { ValidateRequestedParameterNames(new[] { "MOOD" }, new[] { "Mood", "mood" }); return 2; } catch (InvalidOperationException e) { if (e.Message != "Parameter identity is ambiguous: MOOD") return 3; }
        try { ValidateRequestedParameterNames(new[] { "Missing" }, new[] { "Mood" }); return 4; } catch (InvalidOperationException e) { if (e.Message != "Exact parameter not found: Missing") return 5; }
        try { ValidateRequestedParameterNames(new[] { "Mood", "Mood" }, new[] { "Mood" }); return 6; } catch (InvalidOperationException e) { if (e.Message != "Optimization suggestions contain a duplicate parameter identity.") return 7; }
        return 0;
    }
}

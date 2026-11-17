using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using LiteDB;

internal static class AlcomLiteDbReader
{
    private static int Main(string[] args)
    {
        // The write mode only edits a disposable copy prepared by the caller.
        bool remove = args != null && args.Length >= 3 && args[1] == "--remove-exact";
        if (args == null || (!remove && args.Length != 1) || !Path.IsPathRooted(args[0]))
            return Fail("invalid_database_path");
        try
        {
            string path = Path.GetFullPath(args[0]);
            if (remove && !String.Equals(Path.GetFileName(path), "vcc-copy.liteDb", StringComparison.Ordinal))
                return Fail("expected_disposable_database_copy");
            if (!File.Exists(path))
                return Fail("database_not_found");
            Console.OutputEncoding = new UTF8Encoding(false);
            var connection = new ConnectionString
            {
                Filename = path,
                Connection = remove ? ConnectionType.Direct : ConnectionType.Shared,
                ReadOnly = !remove
            };
            using (var database = new LiteDatabase(connection))
            {
                var projects = database.GetCollection("projects");
                if (remove)
                {
                    var paths = new HashSet<string>(StringComparer.Ordinal);
                    for (int index = 2; index < args.Length; index++)
                        paths.Add(args[index]);
                    var ids = new List<BsonValue>();
                    foreach (var document in projects.FindAll())
                    {
                        var value = document["Path"];
                        if (value.IsString && paths.Contains(value.AsString))
                            ids.Add(document["_id"]);
                    }
                    database.BeginTrans();
                    foreach (var id in ids)
                        if (!projects.Delete(id))
                            throw new InvalidOperationException("A selected project row could not be removed.");
                    database.Commit();
                    database.Checkpoint();
                }
                foreach (var document in projects.FindAll())
                {
                    var value = document["Path"];
                    if (value != null && value.IsString && !String.IsNullOrWhiteSpace(value.AsString))
                        Console.WriteLine(JsonEscape(value.AsString));
                }
            }
            return 0;
        }
        catch (Exception exception)
        {
            return Fail(exception.GetType().Name + ":" + exception.Message);
        }
    }

    private static int Fail(string reason)
    {
        Console.Error.WriteLine(reason.Length > 512 ? reason.Substring(0, 512) : reason);
        return 1;
    }

    private static string JsonEscape(string value)
    {
        return JsonSerializer.Serialize(new BsonValue(value));
    }
}

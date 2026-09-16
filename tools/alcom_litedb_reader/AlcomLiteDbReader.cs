using System;
using System.IO;
using System.Text;
using LiteDB;

internal static class AlcomLiteDbReader
{
    private static int Main(string[] args)
    {
        if (args == null || args.Length != 1 || !Path.IsPathRooted(args[0]))
            return Fail("invalid_database_path");
        try
        {
            string path = Path.GetFullPath(args[0]);
            if (!File.Exists(path))
                return Fail("database_not_found");
            Console.OutputEncoding = new UTF8Encoding(false);
            var connection = new ConnectionString
            {
                Filename = path,
                Connection = ConnectionType.Shared,
                ReadOnly = true
            };
            using (var database = new LiteDatabase(connection))
            {
                var projects = database.GetCollection("projects");
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

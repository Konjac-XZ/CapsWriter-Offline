namespace CapsWriter_WinUI.Services;

internal static class RepositoryLocator
{
    public static string? FindRoot()
    {
        string? configured = Environment.GetEnvironmentVariable("CAPSWRITER_ROOT");
        if (IsRoot(configured))
        {
            return Path.GetFullPath(configured!);
        }

        foreach (string origin in new[] { Environment.CurrentDirectory, AppContext.BaseDirectory })
        {
            DirectoryInfo? directory = new(origin);
            for (int depth = 0; directory is not null && depth < 12; depth++, directory = directory.Parent)
            {
                if (IsRoot(directory.FullName))
                {
                    return directory.FullName;
                }
            }
        }

        return null;
    }

    private static bool IsRoot(string? path) =>
        !string.IsNullOrWhiteSpace(path)
        && File.Exists(Path.Combine(path, "core_client.py"))
        && File.Exists(Path.Combine(path, "pyproject.toml"));
}

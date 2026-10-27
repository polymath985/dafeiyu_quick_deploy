using System;
using System.IO;
using System.Text;

// OpenSSH reads this program's stdout through a private pipe.
internal static class SshPassword
{
    private static int Main(string[] args)
    {
        try
        {
            if (args.Length != 1 || args[0].IndexOf("password", StringComparison.OrdinalIgnoreCase) < 0 ||
                Environment.GetEnvironmentVariable("SSH_ASKPASS_PROMPT") == "confirm")
                return 1;

            string path = Environment.GetEnvironmentVariable("DAFEIYU_CREDENTIAL_FILE");
            foreach (string line in File.ReadAllLines(path, Encoding.UTF8))
            {
                if (line.TrimStart().StartsWith("#")) continue;
                int split = line.IndexOf('=');
                if (split < 0 || line.Substring(0, split).Trim() != "SSH_PASSWORD") continue;
                string password = line.Substring(split + 1).Trim();
                if (password.Length == 0) return 1;
                using (var output = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)))
                    output.WriteLine(password);
                return 0;
            }
        }
        catch { return 1; }
        return 1;
    }
}

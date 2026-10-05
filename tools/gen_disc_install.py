"""The disc-install and resource parts of the generated patcher (5 Oct 2026; imported by gen_apply_script.py).

Returns PowerShell source text:

  * `DISC_READER`  - the C# class DcDisc (compiled by Add-Type like the GIF codec): opens an ISO 9660 image
                     (.iso with 2048-byte sectors, raw .bin with 2352-byte MODE1 / MODE2 form 1 sectors, a .cue
                     naming the .bin) or a folder / mounted drive, lists its files and extracts them
  * `disc_data(m)` - the manifest of tools/disc_manifest.json as `$DiscFiles` ('root|to|disc[|from]' per file)
  * `DISC_LOGIC`   - Open-Disc, the two disc tests, Install-GameFromDiscs (every manifest file into the install
                     folder, the derived files, the two originals verified by SHA-256), the resource copy
                     (Copy-Resources from patcher\\game | patcher\\editor into the game folders) and
                     Test-DataFile (a fix's data file counts as present when the resource folder holds it)
"""
import os

DISC_READER = r'''
# =================================================================================================
#  DISCS - the game files from the player's own Dark Colony and Council Wars discs (5 Oct 2026)
#
#  The installer package for ModDB carries nothing of the game itself (that would be Strategic Simulations'
#  copyright); the patcher's resources (patcher\game, patcher\editor) are the project's own files and the
#  ozi_ns mission pack.  A player without a game folder points the installer at the two original discs - a
#  disc image (.iso, .bin, .cue) or a mounted / real drive - and it copies the game into the install folder,
#  then patches it like any other game folder.  Which disc holds which file is the manifest $DiscFiles below
#  (tools/discs.py of the Dark-Colony-Server repository, built from the two CD images against the repository):
#  the Council Wars CD holds ENGEXP16.EXE, the expansion (EXPENG\EXP = exp\) and the Classic data the
#  expansion shares (DC\ANIMATE, AVI, INTRFACE, SOUND, SPRITES, WALLPAPR), the Dark Colony CD the rest of the
#  Classic game (DC\SCENARIO, GAMESTAT, CURSOR, MISSION, ENCYCLO, MAPED.EXE and the Classic movies, written
#  as AVI\DCINTRO.AVI etc. because the expansion's own have the same names).  Both are needed.  Not on the
#  discs: the January 1998 dc16.exe (the untouched Classic exe, which this installer no longer patches), the
#  map editor's Borland runtime DLLs (they ship as resources).  The CD soundtrack IS on the discs - tracks 2-5
#  of both mixed-mode CDs - and is ripped from a .bin / .cue image or a real drive and encoded to MP3 with
#  Windows' own encoder (Install-DiscMusic below); an .iso has no audio tracks.  ISO 9660 level 1 (8.3 upper-case names with ";1" versions, no Joliet); the .bin images are
#  mixed-mode (audio tracks after the data track) - only the data track is read.
# =================================================================================================
$DiscReaderSource = @'
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

// DcDisc: the files of one disc, from an ISO 9660 image or a folder.  Paths are "/DC/ANIM.DAT" (upper case,
// no version suffix), matched case-insensitively.  Extract() copies one file to disk.
public class DcDiscEntry { public string Path; public long Lba; public long Size; public string FilePath; }
public class DcDisc : IDisposable
{
    public string Volume = "";
    public string Source;
    public bool IsFolder;
    public int SectorSize;
    public Dictionary<string, DcDiscEntry> Files = new Dictionary<string, DcDiscEntry>(StringComparer.OrdinalIgnoreCase);
    FileStream f;
    int ss, off;
    static readonly byte[] SYNC = new byte[] { 0, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 0 };

    public static DcDisc Open(string path)
    {
        DcDisc d = new DcDisc();
        d.Source = path;
        if (Directory.Exists(path)) { d.OpenFolder(path); return d; }
        if (!File.Exists(path)) throw new FileNotFoundException("not found: " + path);
        if (path.EndsWith(".cue", StringComparison.OrdinalIgnoreCase))
        {
            string bin = null;
            foreach (string line in File.ReadAllLines(path, Encoding.Default))
            {
                string t = line.Trim();
                if (!t.StartsWith("FILE ", StringComparison.OrdinalIgnoreCase)) continue;
                string rest = t.Substring(5).Trim();
                if (rest.StartsWith("\"")) { int q = rest.IndexOf('"', 1); rest = q > 0 ? rest.Substring(1, q - 1) : rest.Substring(1); }
                else { int sp = rest.IndexOf(' '); if (sp > 0) rest = rest.Substring(0, sp); }
                bin = Path.Combine(Path.GetDirectoryName(Path.GetFullPath(path)), rest);
                break;
            }
            if (bin == null) throw new InvalidDataException(path + ": no FILE line in the cue sheet");
            if (!File.Exists(bin)) throw new FileNotFoundException("the cue sheet names " + bin + ", which is not there");
            path = bin;
        }
        d.f = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 16);
        byte[] head = new byte[16];
        if (d.f.Read(head, 0, 16) < 16) throw new InvalidDataException(path + ": too small for a disc image");
        bool sync = true;
        for (int i = 0; i < 12; i++) if (head[i] != SYNC[i]) sync = false;
        if (sync) { d.ss = 2352; d.off = (head[15] == 2) ? 24 : 16; }
        else { d.ss = 2048; d.off = 0; }
        d.SectorSize = d.ss;
        byte[] pvd = d.Sector(16);
        if (pvd[1] != 'C' || pvd[2] != 'D' || pvd[3] != '0' || pvd[4] != '0' || pvd[5] != '1')
            throw new InvalidDataException(path + ": no ISO 9660 volume descriptor at sector 16 (not a data CD image, or an image format this installer does not read)");
        d.Volume = Encoding.ASCII.GetString(pvd, 40, 32).Trim();
        long rootLba = BitConverter.ToUInt32(pvd, 158);
        long rootLen = BitConverter.ToUInt32(pvd, 166);
        d.Walk(rootLba, rootLen, "");
        return d;
    }

    void OpenFolder(string root)
    {
        IsFolder = true;
        root = Path.GetFullPath(root).TrimEnd('\\', '/');
        foreach (string file in Directory.GetFiles(root, "*", SearchOption.AllDirectories))
        {
            string rel = file.Substring(root.Length).Replace('\\', '/');
            if (!rel.StartsWith("/")) rel = "/" + rel;
            DcDiscEntry e = new DcDiscEntry();
            e.Path = rel.ToUpperInvariant(); e.FilePath = file; e.Size = new FileInfo(file).Length;
            Files[e.Path] = e;
        }
        // a drive letter: the volume label, for the messages
        try { string r = Path.GetPathRoot(root); if (!string.IsNullOrEmpty(r) && r.Length <= 3) Volume = new DriveInfo(r).VolumeLabel; } catch { }
    }

    byte[] Sector(long lba)
    {
        byte[] b = new byte[2048];
        f.Seek(lba * ss + off, SeekOrigin.Begin);
        int n = f.Read(b, 0, 2048);
        return b;
    }

    byte[] ReadExtent(long lba, long size)
    {
        byte[] outb = new byte[size];
        long done = 0;
        while (done < size)
        {
            byte[] s = Sector(lba++);
            int take = (int)Math.Min(2048, size - done);
            Array.Copy(s, 0, outb, done, take);
            done += take;
        }
        return outb;
    }

    void Walk(long lba, long length, string prefix)
    {
        byte[] data = ReadExtent(lba, length);
        int pos = 0;
        while (pos < data.Length)
        {
            int ln = data[pos];
            if (ln == 0) { pos = (pos / 2048 + 1) * 2048; continue; }
            long ext = BitConverter.ToUInt32(data, pos + 2);
            long size = BitConverter.ToUInt32(data, pos + 10);
            int flags = data[pos + 25];
            int nl = data[pos + 32];
            if (nl == 1 && (data[pos + 33] == 0 || data[pos + 33] == 1)) { pos += ln; continue; }
            string name = Encoding.ASCII.GetString(data, pos + 33, nl);
            pos += ln;
            int semi = name.IndexOf(';'); if (semi >= 0) name = name.Substring(0, semi);
            name = name.TrimEnd('.');
            string full = prefix + "/" + name;
            if ((flags & 2) != 0) Walk(ext, size, full);
            else { DcDiscEntry e = new DcDiscEntry(); e.Path = full.ToUpperInvariant(); e.Lba = ext; e.Size = size; Files[e.Path] = e; }
        }
    }

    public bool Has(string path) { return Files.ContainsKey(path); }
    public long SizeOf(string path) { return Files[path].Size; }

    // ---- the audio tracks (the soundtrack: tracks 2-5 of both mixed-mode CDs).  [start, count] in 2352-byte sectors:
    // file sectors of a raw .bin image (found by scanning: the data track is a prefix carrying the sync pattern, the
    // tracks are separated by >= 150 sectors = 2 s of digital silence, pieces under 20 s are gap noise), or LBAs of a
    // real disc in a drive (from its table of contents, read raw with IOCTL_CDROM_RAW_READ).  An .iso and a folder
    // have none.  AudioNote says why when the list is empty.
    public List<long[]> AudioTracks = new List<long[]>();
    public string AudioNote = "";
    IntPtr hDrive = IntPtr.Zero;
    bool isDrive;
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern IntPtr CreateFileW(string name, uint access, uint share, IntPtr sec, uint disp, uint flags, IntPtr tmpl);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool DeviceIoControl(IntPtr h, uint code, byte[] inBuf, int inSize, byte[] outBuf, int outSize, out int returned, IntPtr overlapped);
    [DllImport("kernel32.dll")]
    static extern bool CloseHandle(IntPtr h);

    static bool SilentSector(byte[] buf, int off)
    {
        for (int i = 0; i < 2352; i += 2) { short v = (short)(buf[off + i] | (buf[off + i + 1] << 8)); if (v > 2 || v < -2) return false; }
        return true;
    }
    public void ScanAudio()
    {
        if (IsFolder) { OpenDriveAudio(); return; }
        if (ss != 2352) { AudioNote = "an .iso image holds the data track only - the soundtrack is on the .bin / .cue image or on the disc itself"; return; }
        long total = f.Length / ss;
        long lo = 0, hi = total; byte[] h = new byte[12];
        while (lo < hi)
        {
            long mid = (lo + hi) / 2; f.Seek(mid * ss, SeekOrigin.Begin); f.Read(h, 0, 12);
            bool sync = true; for (int i = 0; i < 12; i++) if (h[i] != SYNC[i]) sync = false;
            if (sync) lo = mid + 1; else hi = mid;
        }
        if (lo >= total) { AudioNote = "no audio tracks after the data track (the image holds the data track only)"; return; }
        const int chunk = 256; byte[] buf = new byte[ss * chunk];
        long pos = lo, runStart = -1, pieceStart = -1;
        List<long[]> pieces = new List<long[]>();
        while (pos < total)
        {
            int n = (int)Math.Min(chunk, total - pos);
            f.Seek(pos * ss, SeekOrigin.Begin); int got = f.Read(buf, 0, n * ss); n = got / ss; if (n <= 0) break;
            for (int i = 0; i < n; i++)
            {
                long sct = pos + i;
                if (SilentSector(buf, i * ss)) { if (runStart < 0) runStart = sct; continue; }
                if (runStart >= 0 && sct - runStart >= 150) { if (pieceStart >= 0) pieces.Add(new long[] { pieceStart, runStart - pieceStart }); pieceStart = sct; }
                else if (pieceStart < 0) pieceStart = sct;
                runStart = -1;
            }
            pos += n;
        }
        if (pieceStart >= 0) { long end = runStart >= 0 ? runStart : total; pieces.Add(new long[] { pieceStart, end - pieceStart }); }
        foreach (long[] pc in pieces) if (pc[1] >= 1500) AudioTracks.Add(pc);
        if (AudioTracks.Count == 0) AudioNote = "no audio tracks found after the data track";
    }
    void OpenDriveAudio()
    {
        string letter = null;
        try { letter = Path.GetPathRoot(Path.GetFullPath(Source)); } catch { }
        if (string.IsNullOrEmpty(letter) || letter.Length > 3) { AudioNote = "a folder has no audio tracks - the soundtrack comes from the disc itself (a drive letter) or its .bin / .cue image"; return; }
        try { if (new DriveInfo(letter).DriveType != DriveType.CDRom) { AudioNote = "not a CD drive (a mounted .iso has the data track only) - the soundtrack comes from the disc itself or its .bin / .cue image"; return; } }
        catch { AudioNote = "the drive type could not be read"; return; }
        hDrive = CreateFileW(@"\\.\" + letter.Substring(0, 2), 0x80000000, 3, IntPtr.Zero, 3, 0, IntPtr.Zero);
        if (hDrive == new IntPtr(-1)) { hDrive = IntPtr.Zero; AudioNote = "the drive cannot be opened for raw reading (Windows error " + Marshal.GetLastWin32Error() + ")"; return; }
        byte[] toc = new byte[804]; int ret;
        if (!DeviceIoControl(hDrive, 0x24000, null, 0, toc, toc.Length, out ret, IntPtr.Zero)) { AudioNote = "the disc's table of contents cannot be read (Windows error " + Marshal.GetLastWin32Error() + ")"; return; }
        int first = toc[2], last = toc[3];
        if (last < first || last - first > 98) { AudioNote = "unreadable table of contents"; return; }
        List<long> starts = new List<long>(); List<bool> audio = new List<bool>();
        for (int t = 0; t <= last - first + 1; t++)        // the tracks and the lead-out entry after them
        {
            int o = 4 + t * 8;
            int control = toc[o + 1] & 0x0F;
            long lba = ((long)toc[o + 5] * 60 + toc[o + 6]) * 75 + toc[o + 7] - 150;
            starts.Add(lba); audio.Add((control & 4) == 0);
        }
        for (int t = 0; t < last - first + 1; t++) if (audio[t] && starts[t + 1] > starts[t]) AudioTracks.Add(new long[] { starts[t], starts[t + 1] - starts[t] });
        isDrive = true;
        if (AudioTracks.Count == 0) AudioNote = "the disc in the drive has no audio tracks";
    }
    byte[] ReadAudioSectors(long start, int count)
    {
        byte[] outb = new byte[count * 2352];
        if (isDrive)
        {
            int done = 0;
            while (done < count)
            {
                int n = Math.Min(16, count - done);
                byte[] req = new byte[16];
                BitConverter.GetBytes((long)((start + done) * 2048)).CopyTo(req, 0);
                BitConverter.GetBytes(n).CopyTo(req, 8); BitConverter.GetBytes(2).CopyTo(req, 12);   // TrackMode CDDA
                byte[] tmp = new byte[n * 2352]; int ret;
                if (!DeviceIoControl(hDrive, 0x2403E, req, 16, tmp, tmp.Length, out ret, IntPtr.Zero)) throw new IOException("raw audio read failed at sector " + (start + done) + " (Windows error " + Marshal.GetLastWin32Error() + ")");
                Array.Copy(tmp, 0, outb, done * 2352, Math.Min(ret, n * 2352)); done += n;
            }
            return outb;
        }
        f.Seek(start * ss, SeekOrigin.Begin); f.Read(outb, 0, outb.Length); return outb;
    }
    static bool SilentFrame(byte[] b, int i)
    {
        short l = (short)(b[i * 4] | (b[i * 4 + 1] << 8)), r = (short)(b[i * 4 + 2] | (b[i * 4 + 3] << 8));
        return l > -3 && l < 3 && r > -3 && r < 3;
    }
    // Writes audio track `index` as a WAV (44.1 kHz, 16-bit stereo - CD audio as it is): the pregap junk at the start is
    // cut at the end of the last >= 20 ms run of digital silence within the first second, trailing silence is dropped.
    // Returns the length in seconds.
    public double WriteTrackWav(int index, string dest)
    {
        long[] t = AudioTracks[index];
        byte[] pcm = ReadAudioSectors(t[0], (int)t[1]);
        int frames = pcm.Length / 4, start = 0, run = 0;
        int firstSecond = Math.Min(44100, frames);
        for (int i = 0; i < firstSecond; i++)
        {
            if (SilentFrame(pcm, i)) { run++; if (run >= 882) start = i + 1; }
            else run = 0;
        }
        int end = frames;
        while (end > start && SilentFrame(pcm, end - 1)) end--;
        int bytes = (end - start) * 4;
        using (FileStream o = new FileStream(dest, FileMode.Create, FileAccess.Write))
        {
            BinaryWriter w = new BinaryWriter(o);
            w.Write(Encoding.ASCII.GetBytes("RIFF")); w.Write(36 + bytes); w.Write(Encoding.ASCII.GetBytes("WAVEfmt "));
            w.Write(16); w.Write((short)1); w.Write((short)2); w.Write(44100); w.Write(44100 * 4); w.Write((short)4); w.Write((short)16);
            w.Write(Encoding.ASCII.GetBytes("data")); w.Write(bytes);
            o.Write(pcm, start * 4, bytes);
        }
        return (end - start) / 44100.0;
    }

    public byte[] Read(string path)
    {
        DcDiscEntry e = Files[path];
        if (IsFolder) return File.ReadAllBytes(e.FilePath);
        return ReadExtent(e.Lba, e.Size);
    }

    // copies one file; returns its size
    public long Extract(string path, string dest)
    {
        DcDiscEntry e = Files[path];
        string dir = Path.GetDirectoryName(dest);
        if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
        if (IsFolder) { File.Copy(e.FilePath, dest, true); return e.Size; }
        using (FileStream o = new FileStream(dest, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 16))
        {
            if (ss == 2048)
            {
                f.Seek(e.Lba * 2048, SeekOrigin.Begin);
                byte[] buf = new byte[1 << 16];
                long left = e.Size;
                while (left > 0) { int n = f.Read(buf, 0, (int)Math.Min(buf.Length, left)); if (n <= 0) break; o.Write(buf, 0, n); left -= n; }
            }
            else
            {
                const int chunk = 64;
                byte[] buf = new byte[ss * chunk];
                long sectors = (e.Size + 2047) / 2048, done = 0, lba = e.Lba;
                while (done < e.Size)
                {
                    int n = (int)Math.Min(chunk, sectors - (lba - e.Lba));
                    f.Seek(lba * ss, SeekOrigin.Begin);
                    int got = f.Read(buf, 0, n * ss);
                    for (int i = 0; i < n && done < e.Size; i++)
                    {
                        int take = (int)Math.Min(2048, e.Size - done);
                        o.Write(buf, i * ss + off, take);
                        done += take;
                    }
                    lba += n;
                }
            }
        }
        return e.Size;
    }

    public void Dispose() { if (f != null) { f.Dispose(); f = null; } if (hDrive != IntPtr.Zero) { CloseHandle(hDrive); hDrive = IntPtr.Zero; } }
}
'@
$script:discReaderReady = $false
function Initialize-DiscReader {
    if ($script:discReaderReady) { return }
    try {
        if (-not ('DcDisc' -as [type])) { Add-Type -TypeDefinition $DiscReaderSource -ErrorAction Stop }
        $script:discReaderReady = $true
    } catch {
        throw ("the disc reader could not be compiled (Add-Type): {0}`r`nIt is C# text in this file, compiled with the .NET compiler that ships with Windows. " +
               "If Add-Type is blocked on your PC, mount the disc images (double-click an .iso) and point the installer at the drive letters instead." -f $_.Exception.Message)
    }
}
'''


def disc_data(m):
    """`$DiscFiles` and the derived-file rules from the manifest."""
    lines = []
    for ent in m['files']:
        root = 'g' if ent['root'] == 'game' else 'e'
        disc = 'c' if ent['disc'] == 'cw' else 'd'
        s = '%s|%s|%s' % (root, ent['to'].replace('/', '\\'), disc)
        if ent.get('from'):
            s += '|' + ent['from']
        lines.append("    '" + s.replace("'", "''") + "'")
    cw, dc = m['discs']['cw'], m['discs']['dc']
    n_cw = sum(1 for e in m['files'] if e['disc'] == 'cw')
    n_dc = len(m['files']) - n_cw
    return ('''
# One line per file the installer takes from the discs: root (g = the game folder, e = the map editor's folder) |
# path in the install folder | disc (c = Council Wars, d = Dark Colony) [| path on the disc when it is not the
# natural one: /DC/<path> for the game folder, /EXPENG/EXP/<path> for exp\\, /DC/SCENARIO/<path> for the editor's
# scenario\\].  %d files: %d from the Council Wars disc (volume %s), %d from the Dark Colony disc (volume %s).
$DiscFiles = @(
%s
)
# the installer derives these (not on a disc as they are): the January 1998 update's repair of mission 9's trigger
# file (the disc's text has a typo, `&&==`), the two drive-letter files the original installers wrote (the patched
# exes never read them, the untouched originals look for the disc on that drive), the editor's own copy
$DiscDerived = @(
    @{ Root = 'g'; To = 'SCENARIO\\HUMAN\\human09.tro'; Disc = 'd'; From = '/DC/SCENARIO/HUMAN/HUMAN09.TRO'; Rule = 'tro_typo' }
    @{ Root = 'g'; To = 'HBNFUFL.A01'; Text = ("D:`r`n" + [char] 0x1A) }
    @{ Root = 'g'; To = 'HBNFUFL.A02'; Text = ("D:`r`n" + [char] 0x1A) }
    @{ Root = 'e'; To = 'hbnfufl.a01'; Text = ("C:`r`n" + [char] 0x1A) }
)
# the two originals the discs provide, verified against the builds' SHA-256 after extraction
$DiscOriginals = @(
    @{ Build = 'CouncilWars'; Disc = 'c'; From = '/EXPENG/ENGEXP16.EXE'; Root = 'g'; To = 'ENGEXP16.EXE' }
    @{ Build = 'MapEditor';   Disc = 'd'; From = '/DC/MAPED.EXE';        Root = 'e'; To = 'maped.exe' }
)
# the layout of a fresh install: the game in the chosen folder, the map editor in a sub-folder
$InstallSubfolder = @{ g = ''; e = 'Map editor' }
''' % (len(m['files']), n_cw, cw['volume'], n_dc, dc['volume'], '\n'.join(lines)))


DISC_LOGIC = r'''
function Get-InstallRoot([string] $Dir, [string] $Root) {
    $sub = $InstallSubfolder[$Root]
    if ($sub) { return (Join-Path $Dir $sub) } else { return $Dir }
}
# the disc path of a manifest line (its 4th field, or the natural place)
function Get-DiscSourcePath([string] $Root, [string] $To, [string] $From) {
    if ($From) { return $From }
    $fwd = $To.Replace('\', '/')
    if ($Root -eq 'e') { if ($fwd.StartsWith('scenario/', [StringComparison]::OrdinalIgnoreCase)) { return '/DC/SCENARIO/' + $fwd.Substring(9) } else { return '/DC/' + $fwd } }
    if ($fwd.StartsWith('exp/', [StringComparison]::OrdinalIgnoreCase)) { return '/EXPENG/EXP/' + $fwd.Substring(4) }
    return '/DC/' + $fwd
}
function Open-Disc([string] $Path, [string] $What) {
    Initialize-DiscReader
    if (-not $Path) { throw "no $What given" }
    $p = $Path.Trim()
    if ($p -match '^[A-Za-z]:$') { $p += '\' }
    if (-not (Test-Path -LiteralPath $p)) { throw "$What not found: $Path" }
    try { $d = [DcDisc]::Open((Get-AbsolutePath $p)) } catch { throw ("{0} cannot be read as a disc: {1}" -f $What, $_.Exception.Message) }
    try { $d.ScanAudio() } catch { $d.AudioNote = 'the audio tracks could not be read: ' + $_.Exception.Message }
    return $d
}

# --- the soundtrack (5 Oct 2026, maintainer: "music is on the original discs as real music disc tracks. installer must
# rip them, and if possible convert to mp3").  Both CDs are mixed-mode: tracks 2-5 are the music the game played from
# the CD; the `music` fix plays MUSIC\TRACK02-05.MP3 (the Dark Colony disc's) and exp\music\track02-05.mp3 (the Council
# Wars disc's) instead.  The installer writes each track as WAV (DcDisc.WriteTrackWav: the raw 2352-byte audio sectors
# ARE 44.1 kHz 16-bit stereo PCM) and encodes it to MP3 at 192 kbit/s with Windows' own encoder through the WinRT
# MediaTranscoder (Windows 8 and later; the "N" editions lack it without the Media Feature Pack).  WinRT is reachable
# from Windows PowerShell 5.1 only, so PowerShell 7 runs the encoder in a powershell.exe child.  The game's MCI player
# (mpegvideo) refuses a WAV under the .mp3 name, so without an encoder the music is left out.
$Mp3TranscoderScript = @'
param([string] $In, [string] $Out)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Transcoding.MediaTranscoder, Windows.Media, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.MediaProperties.MediaEncodingProfile, Windows.Media, ContentType = WindowsRuntime]
$methods = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 }
$asTaskOp = $methods | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
$asTaskActP = $methods | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncActionWithProgress`1' } | Select-Object -First 1
function AwaitOp($op, $type) { $t = $asTaskOp.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(); return $t.Result }
function AwaitAct($op, $type) { $t = $asTaskActP.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait() }
if (-not (Test-Path -LiteralPath $Out)) { [System.IO.File]::WriteAllBytes($Out, [byte[]] @()) }
$src = AwaitOp ([Windows.Storage.StorageFile]::GetFileFromPathAsync([System.IO.Path]::GetFullPath($In))) ([Windows.Storage.StorageFile])
$dst = AwaitOp ([Windows.Storage.StorageFile]::GetFileFromPathAsync([System.IO.Path]::GetFullPath($Out))) ([Windows.Storage.StorageFile])
$profile = [Windows.Media.MediaProperties.MediaEncodingProfile]::CreateMp3([Windows.Media.MediaProperties.AudioEncodingQuality]::High)
$profile.Audio.Bitrate = 192000
$profile.Audio.SampleRate = 44100
$profile.Audio.ChannelCount = 2
$tc = New-Object Windows.Media.Transcoding.MediaTranscoder
$prep = AwaitOp ($tc.PrepareFileTranscodeAsync($src, $dst, $profile)) ([Windows.Media.Transcoding.PrepareTranscodeResult])
if (-not $prep.CanTranscode) { throw ('Windows cannot encode MP3 here (' + $prep.FailureReason + ') - an "N" edition without the Media Feature Pack?') }
AwaitAct ($prep.TranscodeAsync()) ([double])
if ((Get-Item -LiteralPath $Out).Length -lt 1000) { throw 'the encoder wrote an empty file' }
'@
function ConvertTo-Mp3([string] $Wav, [string] $Mp3) {
    if ($PSVersionTable.PSEdition -eq 'Desktop') {
        & ([scriptblock]::Create($Mp3TranscoderScript)) $Wav $Mp3
        return
    }
    # PowerShell 7 has no WinRT projection: the same script in a Windows PowerShell child
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('dc_mp3_' + [System.Diagnostics.Process]::GetCurrentProcess().Id + '.ps1')
    $err = $tmp + '.err'
    [System.IO.File]::WriteAllText($tmp, $Mp3TranscoderScript)
    try {
        $exe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $pr = Start-Process -FilePath $exe -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $tmp + '"'), ('"' + $Wav + '"'), ('"' + $Mp3 + '"')) -Wait -PassThru -WindowStyle Hidden -RedirectStandardError $err
        if ($pr.ExitCode -ne 0) {
            $msg = ''; if (Test-Path -LiteralPath $err) { $msg = ((Get-Content -LiteralPath $err -Raw) -replace '\s+', ' ').Trim() }
            if ($msg.Length -gt 300) { $msg = $msg.Substring(0, 300) }
            throw ('the MP3 encoder (Windows PowerShell child) failed: ' + $msg)
        }
    } finally { foreach ($x in $tmp, $err) { if (Test-Path -LiteralPath $x) { Remove-Item -LiteralPath $x -Force -ErrorAction SilentlyContinue } } }
}
$script:mp3Encoder = $null     # @{ Ok; Note } once tested
function Test-Mp3Encoder {
    if ($script:mp3Encoder) { return $script:mp3Encoder }
    $dir = Join-Path ([System.IO.Path]::GetTempPath()) ('dc_mp3test_' + [System.Diagnostics.Process]::GetCurrentProcess().Id)
    [void] [System.IO.Directory]::CreateDirectory($dir)
    $wav = Join-Path $dir 'probe.wav'; $mp3 = Join-Path $dir 'probe.mp3'
    try {
        # a fifth of a second of silence, 44.1 kHz 16-bit stereo
        $n = 8820 * 4
        $ms = New-Object System.IO.MemoryStream
        $w = New-Object System.IO.BinaryWriter($ms)
        $w.Write([System.Text.Encoding]::ASCII.GetBytes('RIFF')); $w.Write([int](36 + $n)); $w.Write([System.Text.Encoding]::ASCII.GetBytes('WAVEfmt '))
        $w.Write([int]16); $w.Write([int16]1); $w.Write([int16]2); $w.Write([int]44100); $w.Write([int](44100 * 4)); $w.Write([int16]4); $w.Write([int16]16)
        $w.Write([System.Text.Encoding]::ASCII.GetBytes('data')); $w.Write([int]$n); $w.Write((New-Object byte[] $n))
        [System.IO.File]::WriteAllBytes($wav, $ms.ToArray())
        ConvertTo-Mp3 $wav $mp3
        $script:mp3Encoder = @{ Ok = $true; Note = '' }
    } catch {
        $script:mp3Encoder = @{ Ok = $false; Note = $_.Exception.Message }
    } finally { try { [System.IO.Directory]::Delete($dir, $true) } catch { } }
    return $script:mp3Encoder
}
# Rips the four music tracks of each disc into the install folder as MP3.  Returns text lines.
function Install-DiscMusic($Cw, $Dc, [string] $Dir, [scriptblock] $Progress) {
    $lines = @()
    $jobs = @(@{ Disc = $Dc; Name = 'Dark Colony'; Folder = 'MUSIC'; Pattern = 'TRACK{0:D2}.MP3' },
              @{ Disc = $Cw; Name = 'Council Wars'; Folder = 'exp\music'; Pattern = 'track{0:D2}.mp3' })
    foreach ($j in $jobs) {
        $folder = Join-Path (Get-InstallRoot $Dir 'g') $j.Folder
        $have = 0
        foreach ($n in 2..5) { $q = Join-Path $folder ($j.Pattern -f $n); if ((Test-Path -LiteralPath $q) -and (Get-Item -LiteralPath $q).Length -gt 100000) { $have++ } }
        if ($have -eq 4) { $lines += ('{0} soundtrack: the four MP3 files are already in {1}\' -f $j.Name, $j.Folder); continue }
        $d = $j.Disc
        if ($d.AudioTracks.Count -lt 4) {
            $why = if ($d.AudioNote) { $d.AudioNote } else { 'only ' + $d.AudioTracks.Count + ' audio track(s) on this disc' }
            $lines += ('{0} soundtrack NOT written ({1}) - fix music is left out; use the .bin / .cue image or the disc itself' -f $j.Name, $why); continue
        }
        $enc = Test-Mp3Encoder
        if (-not $enc.Ok) { $lines += ('{0} soundtrack NOT written: {1}' -f $j.Name, $enc.Note); continue }
        [void] [System.IO.Directory]::CreateDirectory($folder)
        $done = 0; $secs = 0.0
        for ($i = 0; $i -lt 4; $i++) {
            $mp3 = Join-Path $folder ($j.Pattern -f ($i + 2)); $wav = $mp3 + '.wav'
            if ($Progress) { & $Progress ("Ripping the {0} soundtrack from the disc: track {1} of 4, encoding to MP3 (192 kbit/s)..." -f $j.Name, ($i + 1)) }
            try {
                $secs += $d.WriteTrackWav($i, $wav)
                ConvertTo-Mp3 $wav $mp3
                $done++
            } catch {
                $lines += ('{0} soundtrack: track {1} NOT written: {2}' -f $j.Name, ($i + 2), $_.Exception.Message)
                if (Test-Path -LiteralPath $mp3) { Remove-Item -LiteralPath $mp3 -Force -ErrorAction SilentlyContinue }
                break
            } finally {
                if (Test-Path -LiteralPath $wav) { Remove-Item -LiteralPath $wav -Force -ErrorAction SilentlyContinue }
            }
        }
        if ($done -gt 0) { $lines += ('{0} soundtrack: {1} of 4 tracks ripped from the disc into {2}\ ({3} min of music, MP3 192 kbit/s)' -f $j.Name, $done, $j.Folder, [int][Math]::Round($secs / 60)) }
    }
    return $lines
}
# Is this the Council Wars disc / the Dark Colony disc?  Returns '' when yes, else what is wrong with it.
function Test-CouncilWarsDisc($Disc) {
    if (-not $Disc.Has('/EXPENG/ENGEXP16.EXE')) { return ('no EXPENG\ENGEXP16.EXE on it (volume "{0}", {1} files) - this is not the Council Wars CD' -f $Disc.Volume, $Disc.Files.Count) }
    if (-not $Disc.Has('/EXPENG/EXP/ANIM.DAT')) { return ('EXPENG\EXP\ANIM.DAT missing (volume "{0}") - the expansion data is not on this disc' -f $Disc.Volume) }
    return ''
}
function Test-DarkColonyDisc($Disc) {
    if (-not $Disc.Has('/DC/GAMESTAT/GAMESTAT.TXT') -or -not $Disc.Has('/DC/SCENARIO/HUMAN/HUMAN01.SCN')) { return ('no DC\GAMESTAT and DC\SCENARIO on it (volume "{0}", {1} files) - this is not the Dark Colony CD' -f $Disc.Volume, $Disc.Files.Count) }
    if (-not $Disc.Has('/DC/MAPED.EXE')) { return ('DC\MAPED.EXE missing (volume "{0}") - the map editor is not on this disc' -f $Disc.Volume) }
    return ''
}
# Extracts the two originals (ENGEXP16.EXE, maped.exe) into the install folder and checks their SHA-256 against
# the builds.  Returns @{ CouncilWars = <path>; MapEditor = <path> }; throws when a disc is wrong or an exe is
# another build.  Cheap (1.3 MB), so the window does it when the discs page is left.
function Install-DiscOriginals([string] $CwPath, [string] $DcPath, [string] $Dir) {
    $cw = Open-Disc $CwPath 'the Council Wars disc'; $dc = Open-Disc $DcPath 'the Dark Colony disc'
    try {
        $bad = Test-CouncilWarsDisc $cw; if ($bad) { throw "Council Wars disc: $bad" }
        $bad = Test-DarkColonyDisc $dc;  if ($bad) { throw "Dark Colony disc: $bad" }
        $out = @{}
        # the soundtrack: both discs with four audio tracks and an MP3 encoder on this PC = the music fix will have its files
        $audio = ($cw.AudioTracks.Count -ge 4 -and $dc.AudioTracks.Count -ge 4)
        $note = if ($cw.AudioTracks.Count -lt 4) { 'Council Wars disc: ' + $(if ($cw.AudioNote) { $cw.AudioNote } else { 'only ' + $cw.AudioTracks.Count + ' audio track(s)' }) }
                elseif ($dc.AudioTracks.Count -lt 4) { 'Dark Colony disc: ' + $(if ($dc.AudioNote) { $dc.AudioNote } else { 'only ' + $dc.AudioTracks.Count + ' audio track(s)' }) } else { '' }
        if ($audio) { $enc = Test-Mp3Encoder; if (-not $enc.Ok) { $audio = $false; $note = $enc.Note } }
        $out['Audio'] = $audio; $out['AudioNote'] = $note
        foreach ($o in $DiscOriginals) {
            $disc = if ($o.Disc -eq 'c') { $cw } else { $dc }
            $dest = Join-Path (Get-InstallRoot $Dir $o.Root) $o.To
            [void] $disc.Extract($o.From, $dest)
            $b = $null; foreach ($x in $Builds) { if ($x.Id -eq $o.Build) { $b = $x } }
            $sha = Get-Sha256Hex ([System.IO.File]::ReadAllBytes($dest))
            if ($sha -ne $b.OriginalSha256) {
                throw ("{0} on the disc is not the build these fixes are written for ({1} bytes, SHA-256 {2}; expected {3}). Another pressing or language? The English Council Wars and the UK Dark Colony discs are the known ones." -f $o.From, (Get-Item -LiteralPath $dest).Length, $sha, $b.OriginalSha256)
            }
            $out[$o.Build] = $dest
        }
        return $out
    } finally { $cw.Dispose(); $dc.Dispose() }
}
# Copies the whole game from the two discs into $Dir (every $DiscFiles line, then the derived files).  A file already
# there with the right size is kept (a second run after an interruption only fills the gaps).  Returns
# @{ Files; Bytes; Skipped; Missing = @(disc paths this disc does not have) }.  $Progress gets a line per folder.
function Install-GameFromDiscs([string] $CwPath, [string] $DcPath, [string] $Dir, [scriptblock] $Progress) {
    $cw = Open-Disc $CwPath 'the Council Wars disc'; $dc = Open-Disc $DcPath 'the Dark Colony disc'
    try {
        $bad = Test-CouncilWarsDisc $cw; if ($bad) { throw "Council Wars disc: $bad" }
        $bad = Test-DarkColonyDisc $dc;  if ($bad) { throw "Dark Colony disc: $bad" }
        foreach ($r in 'g', 'e') { [void] [System.IO.Directory]::CreateDirectory((Get-InstallRoot $Dir $r)) }
        $files = 0; $bytes = [long] 0; $skipped = 0; $missing = @(); $lastTop = ''
        $total = $DiscFiles.Count; $i = 0
        foreach ($line in $DiscFiles) {
            $i++
            $f = $line.Split('|')
            $root = $f[0]; $to = $f[1]; $disc = if ($f[2] -eq 'c') { $cw } else { $dc }
            $src = Get-DiscSourcePath $root $to $(if ($f.Count -gt 3) { $f[3] } else { '' })
            $dest = Join-Path (Get-InstallRoot $Dir $root) $to
            $top = ($to -split '\\')[0]
            if ($top -ne $lastTop -and $Progress) { & $Progress ("Copying the game from the discs: {0}\{1} ({2} of {3} files)..." -f $(if ($root -eq 'e') { 'Map editor\' } else { '' }), $top, $i, $total); $lastTop = $top }
            if (-not $disc.Has($src)) { $missing += $src; continue }
            if ((Test-Path -LiteralPath $dest) -and (Get-Item -LiteralPath $dest).Length -eq $disc.SizeOf($src) -and -not $to.EndsWith('.EXE', [StringComparison]::OrdinalIgnoreCase)) { $skipped++; continue }
            $bytes += $disc.Extract($src, $dest); $files++
        }
        foreach ($d in $DiscDerived) {
            $dest = Join-Path (Get-InstallRoot $Dir $d.Root) $d.To
            # ($parent, not $dir: PowerShell variable names are case-insensitive and $Dir is the parameter)
            $parent = Split-Path -Parent $dest; if (-not (Test-Path -LiteralPath $parent)) { [void] [System.IO.Directory]::CreateDirectory($parent) }
            if ($d.ContainsKey('Text')) { [System.IO.File]::WriteAllBytes($dest, $script:latin1.GetBytes($d.Text)); continue }
            $disc = if ($d.Disc -eq 'c') { $cw } else { $dc }
            if (-not $disc.Has($d.From)) { $missing += $d.From; continue }
            $data = $disc.Read($d.From)
            if ($d.Rule -eq 'tro_typo') { $data = $script:latin1.GetBytes($script:latin1.GetString($data).Replace('&&==', '==')) }
            [System.IO.File]::WriteAllBytes($dest, $data); $files++
        }
        foreach ($sub in 'SAVE', 'ESAVE', 'ozisave') { [void] [System.IO.Directory]::CreateDirectory((Join-Path (Get-InstallRoot $Dir 'g') $sub)) }
        $music = @(Install-DiscMusic $cw $dc $Dir $Progress)
        return @{ Files = $files; Bytes = $bytes; Skipped = $skipped; Missing = $missing; Music = $music }
    } finally { $cw.Dispose(); $dc.Dispose() }
}

# =================================================================================================
#  RESOURCES - the project's own files, copied from patcher\game | patcher\editor into the game folders
#
#  Since 5 Oct 2026 the patcher's resources live beside this script (patcher\game: the painted backdrops and HUD
#  frames per size in HD_SRC, the console banks, the re-baked logo banks, the tracer bullets, the icon, the
#  ozi_ns mission pack, the DARK COLONY mode's tables, DEFAULT_SERVER.TXT; patcher\editor: the Borland runtime
#  DLLs of the map editor and the Atlantis block set), not in the game folders.  Before a build is patched they
#  are copied into its folder (Copy-Resources; an identical file is left alone), and a fix's Data file counts as
#  present when the resource folder holds it (Test-DataFile).  The game folder of the repository therefore
#  gets these copies on the maintainer's own runs; they are listed in its .gitignore.
# =================================================================================================
$ResourceRoots = @{ CouncilWars = 'game'; MapEditor = 'editor' }
function Get-ResourceRoot($Build) { return (Join-Path $PSScriptRoot $ResourceRoots[$Build.Id]) }
# A planned disc install (@{ Dir; Cw; Dc }, set by the window's discs page and by the command line): the game files
# are not in the install folder yet when the fixes are checked, but the discs will provide every manifest file at
# Patch - so for that folder a data file counts as present when the manifest lists it.
$script:DiscInstall = $null
$script:discTargets = $null
function Get-DiscTargets {
    if (-not $script:discTargets) {
        $h = New-Object 'System.Collections.Generic.HashSet[string]'
        foreach ($line in $DiscFiles) { $f = $line.Split('|'); [void] $h.Add(($f[0] + '|' + $f[1]).ToUpperInvariant()) }
        foreach ($d in $DiscDerived) { [void] $h.Add(($d.Root + '|' + $d.To).ToUpperInvariant()) }
        $script:discTargets = $h
    }
    return $script:discTargets
}
function Test-DataFile($Build, [string] $GameDir, [string] $Rel) {
    if (Test-Path -LiteralPath (Join-Path $GameDir $Rel)) { return $true }
    if (Test-Path -LiteralPath (Join-Path (Get-ResourceRoot $Build) $Rel)) { return $true }
    if ($script:DiscInstall) {
        $root = if ($ResourceRoots[$Build.Id] -eq 'editor') { 'e' } else { 'g' }
        $expect = [System.IO.Path]::GetFullPath((Get-InstallRoot $script:DiscInstall.Dir $root)).TrimEnd('\')
        if ([System.IO.Path]::GetFullPath($GameDir).TrimEnd('\') -ieq $expect) {
            if ($Rel -match '^(?i)(MUSIC|exp\\music)\\track0[2-5]\.mp3$') { return [bool] $script:DiscInstall.Audio }   # the soundtrack ripped from the discs
            return (Get-DiscTargets).Contains(($root + '|' + $Rel).ToUpperInvariant())
        }
    }
    return $false
}
$script:resourcesCopied = @{}
function Copy-Resources($Build, [string] $GameDir, [scriptblock] $Progress) {
    $src = Get-ResourceRoot $Build
    $key = $src.ToLowerInvariant() + '>' + ([System.IO.Path]::GetFullPath($GameDir)).ToLowerInvariant()
    if ($script:resourcesCopied.ContainsKey($key)) { return @() }
    if (-not (Test-Path -LiteralPath $src)) { return @(('resources NOT copied: the folder {0} beside this script is missing - the fixes that need them are skipped' -f $src)) }
    $n = 0; $same = 0; $total = 0
    foreach ($f in [System.IO.Directory]::GetFiles($src, '*', 'AllDirectories')) {
        $total++
        $rel = $f.Substring($src.TrimEnd('\').Length + 1)
        $dest = Join-Path $GameDir $rel
        if ((Test-Path -LiteralPath $dest) -and (Get-Item -LiteralPath $dest).Length -eq (Get-Item -LiteralPath $f).Length) {
            $a = [System.IO.File]::ReadAllBytes($f); $b = [System.IO.File]::ReadAllBytes($dest)
            if ([System.Linq.Enumerable]::SequenceEqual($a, $b)) { $same++; continue }
        }
        if ($Progress -and ($n % 40) -eq 0) { & $Progress ("Copying the patcher's resources into {0} ({1} of {2} files)..." -f (Split-Path -Leaf $GameDir), $total, @([System.IO.Directory]::GetFiles($src, '*', 'AllDirectories')).Count) }
        $parent = Split-Path -Parent $dest; if (-not (Test-Path -LiteralPath $parent)) { [void] [System.IO.Directory]::CreateDirectory($parent) }
        [System.IO.File]::Copy($f, $dest, $true); $n++
    }
    $script:resourcesCopied[$key] = $true
    return @(('resources: {0} file(s) copied from {1} into the game folder ({2} already there)' -f $n, (Split-Path -Leaf $src), $same))
}
'''

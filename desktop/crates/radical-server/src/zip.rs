//! Zip archives, as far as the project packages need them: written with
//! each member deflated, as Python's `zipfile.ZIP_DEFLATED` writes them, and
//! read back whether stored or deflated -- a package may come from the web
//! app as well as from here.

use miniz_oxide::deflate::compress_to_vec;
use miniz_oxide::inflate::decompress_to_vec_with_limit;

/// The most a package may unpack to: a guard against a small file that
/// inflates without end.
pub const MAX_UNPACKED_BYTES: usize = 512 * 1024 * 1024;

fn crc32(data: &[u8]) -> u32 {
    static TABLE: std::sync::LazyLock<[u32; 256]> = std::sync::LazyLock::new(|| {
        let mut table = [0u32; 256];
        for (i, slot) in table.iter_mut().enumerate() {
            let mut c = i as u32;
            for _ in 0..8 {
                c = if c & 1 == 1 {
                    0xEDB8_8320 ^ (c >> 1)
                } else {
                    c >> 1
                };
            }
            *slot = c;
        }
        table
    });
    !data.iter().fold(!0u32, |c, &b| {
        TABLE[((c ^ u32::from(b)) & 0xFF) as usize] ^ (c >> 8)
    })
}

/// An archive being written, member by member.
#[derive(Default)]
pub struct Writer {
    out: Vec<u8>,
    central: Vec<u8>,
    count: u16,
}

impl Writer {
    pub fn add(&mut self, name: &str, data: &[u8]) {
        let packed = compress_to_vec(data, 6);
        let crc = crc32(data);
        let offset = self.out.len() as u32;
        let name_bytes = name.as_bytes();
        // DOS time and date: 1980-01-01 00:00, as zipfile writes for a
        // member it is given no time for.
        let (time, date) = (0u16, (1 << 5) | 1u16);
        let flags: u16 = 0x0800; // UTF-8 names
        let mut local = Vec::new();
        local.extend(0x0403_4b50u32.to_le_bytes());
        local.extend(20u16.to_le_bytes());
        local.extend(flags.to_le_bytes());
        local.extend(8u16.to_le_bytes());
        local.extend(time.to_le_bytes());
        local.extend(date.to_le_bytes());
        local.extend(crc.to_le_bytes());
        local.extend((packed.len() as u32).to_le_bytes());
        local.extend((data.len() as u32).to_le_bytes());
        local.extend((name_bytes.len() as u16).to_le_bytes());
        local.extend(0u16.to_le_bytes());
        local.extend(name_bytes);
        self.out.extend(&local);
        self.out.extend(&packed);

        self.central.extend(0x0201_4b50u32.to_le_bytes());
        self.central.extend(20u16.to_le_bytes());
        self.central.extend(20u16.to_le_bytes());
        self.central.extend(flags.to_le_bytes());
        self.central.extend(8u16.to_le_bytes());
        self.central.extend(time.to_le_bytes());
        self.central.extend(date.to_le_bytes());
        self.central.extend(crc.to_le_bytes());
        self.central.extend((packed.len() as u32).to_le_bytes());
        self.central.extend((data.len() as u32).to_le_bytes());
        self.central.extend((name_bytes.len() as u16).to_le_bytes());
        self.central.extend([0u8; 12]); // extra, comment, disk, attributes
        self.central.extend(offset.to_le_bytes());
        self.central.extend(name_bytes);
        self.count += 1;
    }

    pub fn finish(mut self) -> Vec<u8> {
        let start = self.out.len() as u32;
        let size = self.central.len() as u32;
        self.out.extend(&self.central);
        self.out.extend(0x0605_4b50u32.to_le_bytes());
        self.out.extend([0u8; 4]);
        self.out.extend(self.count.to_le_bytes());
        self.out.extend(self.count.to_le_bytes());
        self.out.extend(size.to_le_bytes());
        self.out.extend(start.to_le_bytes());
        self.out.extend(0u16.to_le_bytes());
        self.out
    }
}

fn u16_at(b: &[u8], at: usize) -> Option<u16> {
    Some(u16::from_le_bytes(b.get(at..at + 2)?.try_into().ok()?))
}

fn u32_at(b: &[u8], at: usize) -> Option<u32> {
    Some(u32::from_le_bytes(b.get(at..at + 4)?.try_into().ok()?))
}

/// Every member of an archive, by name, in the order the directory lists
/// them; Err when it is not a zip this can read.
pub fn read(bytes: &[u8]) -> Result<Vec<(String, Vec<u8>)>, String> {
    let bad = || "File is not a zip file".to_owned();
    let end = (0..bytes.len().saturating_sub(21))
        .rev()
        .find(|&i| u32_at(bytes, i) == Some(0x0605_4b50))
        .ok_or_else(bad)?;
    let count = u16_at(bytes, end + 10).ok_or_else(bad)? as usize;
    let mut at = u32_at(bytes, end + 16).ok_or_else(bad)? as usize;
    let mut out = Vec::new();
    let mut total = 0usize;
    for _ in 0..count {
        if u32_at(bytes, at) != Some(0x0201_4b50) {
            return Err(bad());
        }
        let method = u16_at(bytes, at + 10).ok_or_else(bad)?;
        let packed = u32_at(bytes, at + 20).ok_or_else(bad)? as usize;
        let size = u32_at(bytes, at + 24).ok_or_else(bad)? as usize;
        let name_len = u16_at(bytes, at + 28).ok_or_else(bad)? as usize;
        let extra_len = u16_at(bytes, at + 30).ok_or_else(bad)? as usize;
        let comment_len = u16_at(bytes, at + 32).ok_or_else(bad)? as usize;
        let local = u32_at(bytes, at + 42).ok_or_else(bad)? as usize;
        let name = bytes.get(at + 46..at + 46 + name_len).ok_or_else(bad)?;
        let name = String::from_utf8_lossy(name).into_owned();
        at += 46 + name_len + extra_len + comment_len;

        if u32_at(bytes, local) != Some(0x0403_4b50) {
            return Err(bad());
        }
        let local_name = u16_at(bytes, local + 26).ok_or_else(bad)? as usize;
        let local_extra = u16_at(bytes, local + 28).ok_or_else(bad)? as usize;
        let start = local + 30 + local_name + local_extra;
        let data = bytes.get(start..start + packed).ok_or_else(bad)?;
        total += size;
        if total > MAX_UNPACKED_BYTES {
            return Err("The package unpacks to more than this app will read.".to_owned());
        }
        let data = match method {
            0 => data.to_vec(),
            8 => decompress_to_vec_with_limit(data, size.max(1))
                .map_err(|_| format!("Bad compressed data in {name}"))?,
            other => {
                return Err(format!(
                    "{name} is compressed in a way (method {other}) this app does not read."
                ));
            }
        };
        out.push((name, data));
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn what_is_written_reads_back() {
        let mut w = Writer::default();
        w.add("a.json", b"{\"x\": 1}");
        w.add(
            "dir/b.csv",
            "name,age\r\nS1,3000\r\n".repeat(100).as_bytes(),
        );
        let members = read(&w.finish()).unwrap();
        assert_eq!(members[0], ("a.json".to_owned(), b"{\"x\": 1}".to_vec()));
        assert_eq!(members[1].1.len(), 1900);
        assert_eq!(crc32(b"123456789"), 0xCBF4_3926);
    }
}

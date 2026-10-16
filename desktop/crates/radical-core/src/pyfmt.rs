//! Python's float formatting, where its text is part of an answer: a curve's
//! grid summary ("1 yr (0-13,900)"), the lines hashed into a curve's content
//! hash, a CSV a user downloads.
//!
//! Both languages round the exact binary value correctly, ties to even, so
//! the digits agree; what differs is the layout -- `%g`'s switch between fixed
//! and exponent notation, its stripped trailing zeros, its two-digit exponent,
//! and the `,` thousands separator. Those are reproduced here.

/// Python's `format(x, '.{precision}g')`, which `'%g'` and `f'{x:g}'` are with
/// a precision of 6.
pub fn g(x: f64, precision: usize) -> String {
    if x.is_nan() {
        return "nan".to_owned();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf" } else { "-inf" }.to_owned();
    }
    let precision = precision.max(1);
    // The value rounded to `precision` significant digits, in exponent form:
    // its exponent decides the notation, as Python's does.
    let rounded = format!("{:.*e}", precision - 1, x);
    let (mantissa, exponent) = rounded.split_once('e').expect("exponent form");
    let exponent: i32 = exponent.parse().expect("an integer exponent");
    if (-4..precision as i32).contains(&exponent) {
        let decimals = (precision as i32 - 1 - exponent) as usize;
        strip_zeros(format!("{x:.decimals$}"))
    } else {
        let mantissa = strip_zeros(mantissa.to_owned());
        let sign = if exponent < 0 { '-' } else { '+' };
        format!("{mantissa}e{sign}{:02}", exponent.abs())
    }
}

/// Trailing zeros after a decimal point dropped, and the point with them.
fn strip_zeros(text: String) -> String {
    if !text.contains('.') {
        return text;
    }
    text.trim_end_matches('0').trim_end_matches('.').to_owned()
}

/// Python's `format(x, ',.0f')`: rounded to a whole number, ties to even,
/// with commas between groups of three digits.
pub fn thousands(x: f64) -> String {
    let whole = format!("{x:.0}");
    let (sign, digits) = match whole.strip_prefix('-') {
        Some(rest) => ("-", rest),
        None => ("", whole.as_str()),
    };
    let mut grouped = String::with_capacity(digits.len() + digits.len() / 3);
    for (i, c) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i) % 3 == 0 {
            grouped.push(',');
        }
        grouped.push(c);
    }
    format!("{sign}{grouped}")
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Python's own output for each value, `format(x, 'g')`.
    const G6: [(f64, &str); 30] = [
        (0.0, "0"),
        (-0.0, "-0"),
        (1.0, "1"),
        (0.5, "0.5"),
        (0.125, "0.125"),
        (0.0001, "0.0001"),
        (1.234e-05, "1.234e-05"),
        (123456.0, "123456"),
        (1234567.0, "1.23457e+06"),
        (999999.5, "1e+06"),
        (9999995.0, "1e+07"),
        (0.000123456, "0.000123456"),
        (1e21, "1e+21"),
        (1e-07, "1e-07"),
        (2.5, "2.5"),
        (3.5, "3.5"),
        (1e16, "1e+16"),
        (123456789.0, "1.23457e+08"),
        (0.1234567, "0.123457"),
        (99999.95, "99999.9"),
        (2450.6, "2450.6"),
        (-12.25, "-12.25"),
        (f64::INFINITY, "inf"),
        (f64::NEG_INFINITY, "-inf"),
        (f64::NAN, "nan"),
        (5e-324, "4.94066e-324"),
        (1.797_693_134_862_315_7e308, "1.79769e+308"),
        (100000.0, "100000"),
        (100000.5, "100000"),
        (9.999995e-05, "0.0001"),
    ];

    #[test]
    fn g_is_pythons() {
        for (x, want) in G6 {
            assert_eq!(g(x, 6), want, "{x:?}");
        }
    }

    #[test]
    fn thousands_is_pythons() {
        for (x, want) in [
            (0.0, "0"),
            (0.5, "0"),
            (1.5, "2"),
            (2.5, "2"),
            (999.5, "1,000"),
            (1000.0, "1,000"),
            (13900.0, "13,900"),
            (55000.0, "55,000"),
            (1234567.4, "1,234,567"),
            (-1234.5, "-1,234"),
            (-0.4, "-0"),
            (10060.0, "10,060"),
        ] {
            assert_eq!(thousands(x), want, "{x:?}");
        }
    }

    /// `format(x, '.4f')` and Rust's `{:.4}` agree, which the CSV relies on.
    #[test]
    fn fixed_is_pythons() {
        for (x, want) in [
            (0.0, "0.0000"),
            (5e-05, "0.0001"),
            (0.00015, "0.0001"),
            (0.27, "0.2700"),
            (-0.53, "-0.5300"),
            (1.08333, "1.0833"),
            (0.12345, "0.1235"),
            (0.99995, "1.0000"),
            (-4e-05, "-0.0000"),
            (123.45675, "123.4567"),
        ] {
            assert_eq!(format!("{x:.4}"), want, "{x:?}");
        }
    }
}

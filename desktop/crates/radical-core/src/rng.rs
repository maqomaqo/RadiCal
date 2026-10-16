//! `numpy.random.default_rng`, reproduced: NumPy's `SeedSequence` turning a
//! seed into PCG64's state, PCG64 itself, and the few draws the services take
//! from it.
//!
//! The services seed every random choice -- a sampler's chains, a KDE's
//! bandwidths, a group calibration's differences -- so that a seed reproduces
//! a run. That promise is about the numbers, not only their distribution, so
//! the port reproduces the generator bit for bit rather than substituting
//! another of the same quality. The corpus (`units/rng`) holds this to
//! NumPy's own state and draws for several seeds.
//!
//! Only what the ported code uses is here: `random`, `uniform` and `choice`
//! with probabilities. NumPy's normal and exponential draws go through
//! ziggurat tables and are not reproduced.

/// What a generator can be seeded with: one non-negative integer, or a
/// sequence of them, as `default_rng(42)` and `default_rng([seed, index])`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Seed {
    One(u128),
    Many(Vec<u128>),
}

impl From<u64> for Seed {
    fn from(value: u64) -> Self {
        Self::One(u128::from(value))
    }
}

impl From<&[u64]> for Seed {
    fn from(values: &[u64]) -> Self {
        Self::Many(values.iter().map(|&v| u128::from(v)).collect())
    }
}

/// An integer as NumPy coerces it to entropy: its 32-bit words, least
/// significant first, and a single zero word for zero.
fn words(value: u128) -> Vec<u32> {
    if value == 0 {
        return vec![0];
    }
    let mut out = Vec::new();
    let mut rest = value;
    while rest > 0 {
        out.push(rest as u32);
        rest >>= 32;
    }
    out
}

const POOL_SIZE: usize = 4;
const INIT_A: u32 = 0x43b0_d7e5;
const MULT_A: u32 = 0x931e_8875;
const INIT_B: u32 = 0x8b51_f9dd;
const MULT_B: u32 = 0x58f3_8ded;
const MIX_MULT_L: u32 = 0xca01_f9dd;
const MIX_MULT_R: u32 = 0x4973_f715;
const XSHIFT: u32 = 16;

/// `numpy.random.SeedSequence`: the seed's words hashed into a pool of four,
/// from which any amount of state is drawn.
pub struct SeedSequence {
    pool: [u32; POOL_SIZE],
}

impl SeedSequence {
    pub fn new(seed: &Seed) -> Self {
        let entropy: Vec<u32> = match seed {
            Seed::One(v) => words(*v),
            Seed::Many(vs) => vs.iter().flat_map(|&v| words(v)).collect(),
        };
        let mut hash_const = INIT_A;
        let mut hashmix = |value: u32| -> u32 {
            let mut value = value ^ hash_const;
            hash_const = hash_const.wrapping_mul(MULT_A);
            value = value.wrapping_mul(hash_const);
            value ^ (value >> XSHIFT)
        };
        let mix = |x: u32, y: u32| -> u32 {
            let result = MIX_MULT_L
                .wrapping_mul(x)
                .wrapping_sub(MIX_MULT_R.wrapping_mul(y));
            result ^ (result >> XSHIFT)
        };

        let mut pool = [0u32; POOL_SIZE];
        for (i, slot) in pool.iter_mut().enumerate() {
            *slot = hashmix(entropy.get(i).copied().unwrap_or(0));
        }
        for src in 0..POOL_SIZE {
            for dst in 0..POOL_SIZE {
                if src != dst {
                    pool[dst] = mix(pool[dst], hashmix(pool[src]));
                }
            }
        }
        for &word in entropy.iter().skip(POOL_SIZE) {
            for slot in &mut pool {
                *slot = mix(*slot, hashmix(word));
            }
        }
        Self { pool }
    }

    /// `generate_state(n, np.uint64)`.
    pub fn generate_state_u64(&self, n: usize) -> Vec<u64> {
        let mut hash_const = INIT_B;
        let words: Vec<u32> = (0..2 * n)
            .map(|i| {
                let mut value = self.pool[i % POOL_SIZE] ^ hash_const;
                hash_const = hash_const.wrapping_mul(MULT_B);
                value = value.wrapping_mul(hash_const);
                value ^ (value >> XSHIFT)
            })
            .collect();
        words
            .chunks(2)
            .map(|pair| u64::from(pair[0]) | (u64::from(pair[1]) << 32))
            .collect()
    }
}

const PCG_MULTIPLIER: u128 = (2_549_297_995_355_413_924 << 64) | 4_865_540_595_714_422_341;

/// NumPy's PCG64: a 128-bit LCG with the XSL-RR output function.
#[derive(Debug, Clone)]
pub struct Pcg64 {
    state: u128,
    inc: u128,
}

impl Pcg64 {
    /// Seeded as `numpy.random.PCG64(seed)` seeds itself.
    pub fn new(seed: &Seed) -> Self {
        let words = SeedSequence::new(seed).generate_state_u64(4);
        let initstate = (u128::from(words[0]) << 64) | u128::from(words[1]);
        let initseq = (u128::from(words[2]) << 64) | u128::from(words[3]);
        let mut rng = Self {
            state: 0,
            inc: (initseq << 1) | 1,
        };
        rng.step();
        rng.state = rng.state.wrapping_add(initstate);
        rng.step();
        rng
    }

    /// The 128-bit state and increment, as NumPy's `bit_generator.state`
    /// reports them.
    pub fn state(&self) -> (u128, u128) {
        (self.state, self.inc)
    }

    fn step(&mut self) {
        self.state = self
            .state
            .wrapping_mul(PCG_MULTIPLIER)
            .wrapping_add(self.inc);
    }

    pub fn next_u64(&mut self) -> u64 {
        self.step();
        let folded = ((self.state >> 64) as u64) ^ (self.state as u64);
        folded.rotate_right((self.state >> 122) as u32)
    }

    /// A double in [0, 1) from the top 53 bits, as NumPy's `next_double`.
    pub fn next_f64(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 * (1.0 / 9_007_199_254_740_992.0)
    }
}

/// `numpy.random.Generator` over PCG64: what `default_rng(seed)` returns.
#[derive(Debug, Clone)]
pub struct Generator {
    bits: Pcg64,
}

impl Generator {
    pub fn new(seed: impl Into<Seed>) -> Self {
        Self {
            bits: Pcg64::new(&seed.into()),
        }
    }

    pub fn bit_generator(&self) -> &Pcg64 {
        &self.bits
    }

    /// `rng.random()`: one double in [0, 1).
    pub fn random(&mut self) -> f64 {
        self.bits.next_f64()
    }

    /// `rng.random(n)`.
    pub fn random_n(&mut self, n: usize) -> Vec<f64> {
        (0..n).map(|_| self.random()).collect()
    }

    /// `rng.uniform(low, high)`: `low + (high - low) * random()`. NumPy refuses
    /// `high < low`; no caller here passes one.
    pub fn uniform(&mut self, low: f64, high: f64) -> f64 {
        debug_assert!(high >= low, "high - low < 0");
        low + (high - low) * self.random()
    }

    /// `rng.choice(len(p), p=p)`: the index whose cumulative probability
    /// first exceeds one uniform draw, the cumulative sum normalised by its
    /// last value as NumPy does. `p` must be non-empty with a positive sum.
    pub fn choice(&mut self, p: &[f64]) -> usize {
        let mut cdf = crate::numeric::cumsum(p);
        let last = *cdf.last().expect("choice from nothing");
        for v in &mut cdf {
            *v /= last;
        }
        let u = self.random();
        crate::numeric::searchsorted_right(&cdf, u)
    }
}

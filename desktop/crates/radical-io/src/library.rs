//! The curves an import may name: the three built in, and the stored curves
//! the user has added. The Python asks its database for these by owner; the
//! desktop app has one user, and hands them over as a value.

use radical_core::curve_registry::{self, BUILTINS, MixRecipe, StoredNames};
use radical_core::measurement::{Builtins, Curves};
use radical_core::{CalibrationCurve, Error};

/// One stored curve, as the importers see it.
#[derive(Debug, Clone, PartialEq)]
pub struct StoredCurve {
    pub names: StoredNames,
    /// Hidden from the pickers, but still resolvable: a file naming it reads.
    pub archived: bool,
    /// For a mixed curve, what it is a mixture of.
    pub recipe: Option<MixRecipe>,
    /// For an imported curve, the file it came from.
    pub source_filename: Option<String>,
}

/// The curves an import resolves names against, oldest stored curve first,
/// and the curve data behind them for the one reader that needs numbers
/// (R_Simulate).
pub struct Library<'a> {
    stored: Vec<StoredCurve>,
    names: Vec<StoredNames>,
    recipes: Vec<MixRecipe>,
    curves: &'a dyn Curves,
}

impl Library<'static> {
    /// Only the curves built in.
    pub fn builtins() -> Self {
        Library::new(Vec::new(), &Builtins)
    }
}

impl<'a> Library<'a> {
    pub fn new(stored: Vec<StoredCurve>, curves: &'a dyn Curves) -> Self {
        let names = stored.iter().map(|s| s.names.clone()).collect();
        let recipes = stored.iter().filter_map(|s| s.recipe.clone()).collect();
        Self {
            stored,
            names,
            recipes,
            curves,
        }
    }

    /// A name as written in a file, as the curve id it means
    /// (`curve_registry.resolve_alias`). Archived curves answer too.
    pub fn resolve_alias(&self, name: &str) -> Option<String> {
        curve_registry::resolve_alias(name, &self.names)
    }

    /// Every curve's display name, builtins first (`list_specs`).
    pub fn curve_names(&self, include_archived: bool) -> Vec<&str> {
        BUILTINS
            .iter()
            .map(|b| b.name)
            .chain(
                self.stored
                    .iter()
                    .filter(|s| include_archived || !s.archived)
                    .map(|s| s.names.name.as_str()),
            )
            .collect()
    }

    /// The stored mixed curve made from this recipe, if there is one.
    pub fn find_mixed(
        &self,
        a: &str,
        b: &str,
        percent_a: f64,
        percent_a_error: f64,
    ) -> Option<String> {
        curve_registry::find_mixed(&self.recipes, a, b, percent_a, percent_a_error)
            .map(str::to_owned)
    }

    /// A stored curve by id.
    pub fn stored(&self, curve_id: &str) -> Option<&StoredCurve> {
        self.stored.iter().find(|s| s.names.id == curve_id)
    }

    pub fn curve(&self, curve_id: &str) -> Result<&CalibrationCurve, Error> {
        self.curves.curve(curve_id)
    }
}

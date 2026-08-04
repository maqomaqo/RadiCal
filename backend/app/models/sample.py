# DEAD CODE -- do not import from here.
#
# This module defines a second, incompatible `RadiocarbonSample` mapped to
# the SAME table ("radiocarbon_samples") as the real one in
# `app/models.py`, but with different columns (Enum sample_type instead of
# String, no ForeignKey/relationship to Analysis, no default UUID id
# generator). Nothing in the codebase imports `app.models.sample` --
# every real usage is `from app.models import RadiocarbonSample`. Having
# two conflicting model classes for the same table is a landmine: if
# anything ever imports this one instead, SQLAlchemy mapper registration
# and queries would silently diverge from the real schema.
#
# This file's contents can't be deleted outright in the environment this
# was authored in (the mount disallows unlinking files), so the class has
# been removed here instead -- if you're looking for the real
# RadiocarbonSample model, it's in app/models.py.

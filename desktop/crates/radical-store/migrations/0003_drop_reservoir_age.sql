-- Version 3: reservoir_age off a collection's dates and an analysis's copies
-- of them, as the backend's migration 9a4e2c7b1d38 does. It predates delta_r,
-- which replaced it, and no calibration ever read it. marine_reservoir_points
-- keeps its own reservoir_age: that is the marine database's field.

ALTER TABLE project_samples DROP COLUMN reservoir_age;
ALTER TABLE radiocarbon_samples DROP COLUMN reservoir_age;

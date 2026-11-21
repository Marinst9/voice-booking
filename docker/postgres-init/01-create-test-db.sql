-- Runs once, the first time the database container starts with an empty volume.
-- Creates a separate database for the automated tests, so they never touch real bookings.
CREATE DATABASE booking_test;

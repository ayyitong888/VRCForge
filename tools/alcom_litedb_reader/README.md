# ALCOM LiteDB reader

This directory contains the source for the bounded Windows helper used to
read the shared VCC and ALCOM `vcc.liteDb` project collection. Given one
absolute database path, it opens the database read-only and emits project paths
as JSON Lines. For confirmed project deletion, the catalogue service prepares
a disposable `vcc-copy.liteDb` and passes `--remove-exact` with the stored
project paths to remove. The service verifies the copy before replacing the
original. `LiteDB.dll` is supplied by the release build from the official MIT
licensed LiteDB 5.x package.

The pinned package is LiteDB 5.0.21, SHA-256
`938a4f5f9d6a28383de8ccfcbdb8897e78432399215232cf38dbf147e03ec167`.

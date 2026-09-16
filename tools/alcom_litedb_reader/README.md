# ALCOM LiteDB reader

This directory contains the source for the bounded Windows helper used to
read the official vrc-get/ALCOM `vcc.liteDb` project collection. The helper
is read-only, accepts exactly one absolute database path, emits JSON Lines,
and never writes or migrates the database. `LiteDB.dll` is supplied by the
release build from the official MIT licensed LiteDB 5.x package.

The pinned package is LiteDB 5.0.21, SHA-256
`938a4f5f9d6a28383de8ccfcbdb8897e78432399215232cf38dbf147e03ec167`.

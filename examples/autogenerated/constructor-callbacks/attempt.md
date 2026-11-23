# Constructors carrying checked callbacks

`fixtures/closures/ConstructorCallbacks.idric` is the source for this attempt. The compiler emits three constructor alternatives with two, one, and zero fields. The two-field arm applies its second writer before its first, then reuses the first writer. One captured byte comes from native integer addition. The one-field and zero-field arms produce different writes.

Run `scripts/run_checked_closures.sh` with `IDRIC_REPO` pointing to a bootstrapped isomorphisms/Idric checkout at `29420f7e5a954ba0a66f622f0311298f823fbfff`. The Linux executable writes `!XYABACZD.` and exits 0. The listing records which constructor each call selected. The same artifact enters the NetBSD cross-generation check.

The compiler's 158-definition `BinaryFilePrefix.idric` witness uses the same operation to dismantle its nested HasIO and Monad dictionaries. At the parent backend revision `351300e`, the first unsupported operation is the constructor pattern for `_builtin.CONS` in `BinaryFilePrefix.main`. After constructor specialization, the application reaches `System.prim__getArgCount` while building the runtime argument list. File access still depends on lowering the rest of that path.

The pinned compiler also emits a zero-argument `CapturedClosure.main` that returns a partially applied IO action. Its compiler-generated `{__mainExpression:0}` passes that action through `PrimIO.unsafePerformIO` and creates the world with `%MkWorld`. Executing that checked entry yields `AB`.

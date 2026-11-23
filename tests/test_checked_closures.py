"""IR-level tests, not compiler-output witnesses.

The real source/checked-artifact acceptance is scripts/run_checked_closures.sh.
"""
import unittest

from backend.idric_x86 import (
    Apply, ArtifactError, Call, ConstCase, Erased, FunctionDef, KnownClosureSpecializer,
    Let, Literal, Operation, Partial, UnsupportedError, Var, parse_expression,
)

PUT = "Prelude.IO.prim__putChar"


class KnownClosureTests(unittest.TestCase):
    def specialize(self, body, *functions):
        main = FunctionDef("M.main", (0,), "", body)
        return KnownClosureSpecializer(
            {function.name: function for function in (main, *functions)}, main.name
        ).run()

    def with_constructors(self, body):
        main = FunctionDef("M.main", (0,), body)
        return KnownClosureSpecializer({main.name: main}, main.name, {"M.Pair": 2, "M.Empty": 0}).run()

    def test_known_constructor_fields_and_default(self):
        for constructed, expected, row in (
                ("%con M.Pair(66, 65)", 65, "constructor_match"),
                ("%con M.Empty()", 67, "constructor_default")):
            body = ("%let v1 = (" + constructed + ") in (%case v1 of { "
                    "%conalt M.Pair(v2, v3) => Prelude.IO.prim__putChar(v3, v0) "
                    "Just Prelude.IO.prim__putChar(67, v0) })")
            expression, rows = self.with_constructors(body)
            self.assertEqual(expression.value, Call(PUT, (Literal(expected), Var(0))))
            self.assertEqual(expression.body, Var(expression.variable))
            self.assertIn(row, [event.realization for event in rows])

    def test_constructor_guards(self):
        pair = "%let v1 = (%con M.Pair([__], 1)) in ("
        failures = (
            ("%con M.Unknown()", "unsupported_constructor"),
            ("%con M.Pair(1)", "constructor_arity"),
            (pair + "%case v1 of { %conalt M.Pair(v2) => 1 Nothing })", "constructor_arity"),
            (pair + "%case v1 of { %conalt M.Pair(v2, v2) => 1 Nothing })", "malformed_constructor_fields"),
            (pair + "%case v1 of { %conalt M.Empty() => 1| %conalt M.Empty() => 2 Nothing })", "duplicate_constructor_arm"),
            (pair + "%case v1 of { %conalt M.Empty() => 9 Nothing })", "unmatched_constructor"),
            ("%case [__] of { %conalt M.Empty() => 9 Nothing }", "unknown_constructor_scrutinee"),
            (pair + "%case v1 of { %conalt M.Pair(v2, v3) => Prelude.IO.prim__putChar(v2, v0) Nothing })", "observable_erased_value"),
            (pair + "%op +Int(v1, 1))", "observable_constructor"),
            (pair + "Prelude.IO.prim__putChar(65, v1))", "observable_constructor"),
            (pair + "v1)", "escaping_constructor"),
        )
        for body, code in failures:
            with self.subTest(code=code, body=body), self.assertRaises((ArtifactError, UnsupportedError)) as caught:
                self.with_constructors(body)
            self.assertEqual(caught.exception.code, code)

    def test_exact_compiler_spelling(self):
        self.assertEqual(parse_expression("<OrderedClosure.{main:0} underapp 2>()"),
                         Partial("OrderedClosure.{main:0}", 2, ()))
        self.assertEqual(parse_expression("v2 @ ([__])"), Apply(Var(2), Erased()))
        self.assertEqual(parse_expression("___"), Erased())
        self.assertEqual(parse_expression("%MkWorld"), Erased())
        self.assertEqual(parse_expression("<M.f underapp 1>(v4, [__])"),
                         Partial("M.f", 1, (Var(4), Erased())))

    def test_saturation_keeps_exact_target_and_order(self):
        # The two equally-ary definitions intentionally have different bodies.
        first = FunctionDef("M.first", (0, 1, 2), "",
                            Let(3, Call(PUT, (Var(0), Var(2))), Call(PUT, (Var(1), Var(2)))))
        second = FunctionDef("M.second", (0, 1, 2), "",
                             Let(3, Call(PUT, (Var(1), Var(2))), Call(PUT, (Var(0), Var(2)))))
        def with_target(target):
            body = Let(1, Partial(target, 2, (Literal(65),)),
                       Let(2, Apply(Var(1), Literal(66)), Apply(Var(2), Var(0))))
            return self.specialize(body, first, second)
        for target, expected in (("M.first", (65, 66)), ("M.second", (66, 65))):
            expression, rows = with_target(target)
            self.assertEqual(expression.value, Call(PUT, (Literal(expected[0]), Var(0))))
            self.assertEqual(expression.body.value, Call(PUT, (Literal(expected[1]), Var(0))))
            self.assertEqual([row.item for row in rows if row.realization == "closure_saturated"],
                             [target])

    def test_effectful_capture_evaluated_once(self):
        writer = FunctionDef("M.writer", (0, 1), "", Call(PUT, (Var(0), Var(1))))
        expression, _ = self.specialize(
            Let(1, Call(PUT, (Literal(65), Var(0))),
                Let(2, Partial("M.writer", 1, (Var(1),)),
                    Let(3, Apply(Var(2), Var(0)), Apply(Var(2), Var(0))))),
            writer)
        # The capture is a single existing binding, not a copied call expression.
        self.assertEqual(expression.value, Call(PUT, (Literal(65), Var(0))))
        self.assertEqual(expression.body.value, Call(PUT, (Var(expression.variable), Var(0))))
        self.assertEqual(expression.body.body.value, expression.body.value)

    def test_erased_argument_can_be_forwarded_and_ignored(self):
        function = FunctionDef("M.f", (0, 1, 2), "", Call(PUT, (Var(1), Var(2))))
        body = Let(1, Partial("M.f", 2, (Erased(),)),
                   Let(2, Apply(Var(1), Literal(65)), Apply(Var(2), Var(0))))
        expression, _ = self.specialize(body, function)
        self.assertEqual(expression.value, Call(PUT, (Literal(65), Var(0))))

    def test_observable_erasure_rejected_even_when_forwarded(self):
        forward = FunctionDef("M.forward", (0, 1), "", Call(PUT, (Var(0), Var(1))))
        for body in (Call(PUT, (Erased(), Var(0))),
                     Call(PUT, (Var(0), Var(0))),
                     Call(PUT, (parse_expression("%MkWorld"), Var(0))),
                     Operation("+Int", (Erased(), Literal(1))),
                     ConstCase(Var(0), ((0, Literal(0)),), None),
                     Apply(Erased(), Literal(0)),
                     Call("M.forward", (Erased(), Var(0)))):
            with self.subTest(body=body), self.assertRaises(UnsupportedError) as caught:
                self.specialize(body, forward)
            self.assertEqual(caught.exception.code, "observable_erased_value")

    def test_malformed_arity_rejected(self):
        function = FunctionDef("M.f", (0, 1), "", Literal(0))
        for missing in (0, 3):
            with self.subTest(missing=missing), self.assertRaises(ArtifactError) as caught:
                self.specialize(Partial("M.f", missing, ()), function)
            self.assertEqual(caught.exception.code, "partial_application_arity")

    def test_closure_cannot_be_used_as_scalar(self):
        function = FunctionDef("M.f", (0,), "", Literal(0))
        with self.assertRaises(UnsupportedError) as caught:
            self.specialize(Let(1, Partial("M.f", 1, ()),
                                Operation("+Int", (Var(1), Literal(0)))), function)
        self.assertEqual(caught.exception.code, "observable_closure_value")


if __name__ == "__main__":
    unittest.main()

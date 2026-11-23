# Mutation operators follow the Java decisions

C uses the same operator set the upstream tool applies to Java, spelled in C: `+`↔`-`, `*`↔`/`, `>`↔`>=`, `<`↔`<=`, `==`↔`!=`, `&&`↔`||`, `0`↔`1`, `true`↔`false`, removal of unary `!`, and removal of unary `-`.

C-specific bitwise and shift operators are not mutated. They were considered and rejected for the first port because they multiply site counts on hardware code without changing the test-quality signal more than the arithmetic and logical sets already do.

# C-only scope

The upstream Python tool mutates Clojure, Java, Go, TypeScript, Rust, and Python. mutate4C exists for C projects, so the fork keeps only the C language module; C++ is out of scope.

Consequences: function names and namespaces come from crap4C's C module, so the two snapshots join on the same keys.

CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    amount NUMERIC(10, 2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO users (name) VALUES
    ('张三'), ('李四'), ('王五');

INSERT INTO orders (user_id, amount) VALUES
    (1, 99.90),
    (1, 150.00),
    (2, 30.50),
    (3, 200.00),
    (2, 88.80);

-- Dedicated evaluation DB only; never apply to business data.
TRUNCATE orders, users RESTART IDENTITY CASCADE;
INSERT INTO users(name,created_at) VALUES
 ('张三','2026-01-01'),('李四','2026-01-02'),('王五','2026-01-03'),
 ('无订单用户','2026-01-04'),('同名用户','2026-01-05'),('同名用户','2026-01-06');
INSERT INTO orders(user_id,amount,created_at) VALUES
 (1,99.90,'2026-01-01'),(1,150.00,'2026-02-01'),(2,30.50,'2026-01-05'),
 (3,200.00,'2026-03-01'),(2,88.80,'2026-02-10'),(5,10.00,'2026-01-10'),
 (6,20.00,'2026-01-11'),(1,0.00,'2026-03-02');

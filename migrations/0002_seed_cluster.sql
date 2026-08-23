INSERT INTO clusters (name, redis_url)
VALUES ('us-east', 'redis://redis:6379/0')
ON CONFLICT (name) DO NOTHING;

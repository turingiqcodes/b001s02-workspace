# Generated so nobody has to type or paste a DB password.
# Letters and digits only: RDS rejects / @ " and spaces, and this keeps the
# Airflow connection string free of URL-encoding problems.
resource "random_password" "db" {
  length  = 24
  special = false
}

resource "aws_db_subnet_group" "airflow" {
  name        = "${var.student_id}-airflow-db-subnets"
  description = "Private subnets for the Airflow metadata DB"
  subnet_ids  = var.db_subnet_ids

  tags = {
    Name = "${var.student_id}-airflow-db-subnets"
  }
}

resource "aws_db_instance" "airflow" {
  identifier     = "${var.student_id}-airflow-db"
  engine         = "postgres"
  engine_version = "16"
  instance_class = "db.t3.micro"

  allocated_storage = 20
  storage_type      = "gp3"

  db_name  = "airflow"
  username = "airflow"
  password = random_password.db.result

  db_subnet_group_name   = aws_db_subnet_group.airflow.name
  vpc_security_group_ids = [aws_security_group.airflow_rds.id]
  publicly_accessible    = false
  multi_az               = false

  # Exercise settings: fast to create, nothing left behind on destroy.
  backup_retention_period = 0
  skip_final_snapshot     = true
  deletion_protection     = false
  apply_immediately       = true

  tags = {
    Name = "${var.student_id}-airflow-db"
  }
}

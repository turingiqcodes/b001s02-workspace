# ---------- Security group for the Airflow EC2 instance ----------
resource "aws_security_group" "airflow_ec2" {
  name        = "${var.student_id}-airflow-ec2-sg"
  description = "SSH and Airflow UI from my IP only"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.student_id}-airflow-ec2-sg"
  }
}

resource "aws_vpc_security_group_ingress_rule" "ssh_from_me" {
  security_group_id = aws_security_group.airflow_ec2.id
  description       = "SSH from my IP"
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  cidr_ipv4         = var.my_ip_cidr

  tags = {
    Name = "${var.student_id}-ssh-from-me"
  }
}

resource "aws_vpc_security_group_ingress_rule" "airflow_ui_from_me" {
  security_group_id = aws_security_group.airflow_ec2.id
  description       = "Airflow web UI from my IP"
  ip_protocol       = "tcp"
  from_port         = 8080
  to_port           = 8080
  cidr_ipv4         = var.my_ip_cidr

  tags = {
    Name = "${var.student_id}-airflow-ui-from-me"
  }
}

resource "aws_vpc_security_group_egress_rule" "ec2_all_out" {
  security_group_id = aws_security_group.airflow_ec2.id
  description       = "All outbound (package installs, RDS on 5432)"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"

  tags = {
    Name = "${var.student_id}-ec2-all-out"
  }
}

# ---------- Security group for the RDS database ----------
resource "aws_security_group" "airflow_rds" {
  name        = "${var.student_id}-airflow-rds-sg"
  description = "Postgres from the Airflow instance only"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.student_id}-airflow-rds-sg"
  }
}

resource "aws_vpc_security_group_ingress_rule" "pg_from_ec2" {
  security_group_id            = aws_security_group.airflow_rds.id
  description                  = "Postgres from Airflow EC2 security group"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.airflow_ec2.id

  tags = {
    Name = "${var.student_id}-pg-from-ec2"
  }
}

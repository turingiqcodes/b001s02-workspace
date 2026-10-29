resource "aws_key_pair" "airflow" {
  key_name   = "${var.student_id}-airflow-key"
  public_key = file(pathexpand(var.ssh_public_key_path))

  tags = {
    Name = "${var.student_id}-airflow-key"
  }
}

resource "random_password" "airflow_admin" {
  length  = 20
  special = false
}

resource "aws_instance" "airflow" {
  ami                         = var.ami_id
  instance_type               = var.instance_type
  subnet_id                   = var.public_subnet_id
  vpc_security_group_ids      = [aws_security_group.airflow_ec2.id]
  key_name                    = aws_key_pair.airflow.key_name
  associate_public_ip_address = true

  root_block_device {
    volume_size = 20
    volume_type = "gp3"

    tags = {
      Name      = "${var.student_id}-airflow-root"
      StudentID = var.student_id
      Purpose   = "terraform-exercise"
    }
  }

  metadata_options {
    http_tokens = "required"
  }

  # Installs Airflow on first boot and points it at the RDS database.
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    db_host        = aws_db_instance.airflow.address
    db_name        = aws_db_instance.airflow.db_name
    db_user        = aws_db_instance.airflow.username
    db_password    = random_password.db.result
    admin_password = random_password.airflow_admin.result
  })
  user_data_replace_on_change = true

  tags = {
    Name = "${var.student_id}-airflow-ec2"
  }
}
